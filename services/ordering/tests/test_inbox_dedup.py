"""Durable inbox deduplication for events Ordering consumes (ADR-0008)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from messaging_conformance.enums import (
    JetStreamConsumerAction,
    QuarantineRedeliveryPolicy,
)
from ordering_fixtures import build_store, inbox_service

from ordering.application.inbox_service import InboxService
from ordering.domain.messaging import InboxRecord, InboxStatus

CONSUMER = "ordering_shipment_custody_v1"
OTHER_CONSUMER = "ordering_delivery_arrival_v1"


def _deliver(service: InboxService, calls: list, *, event_id, consumer=CONSUMER):
    return service.handle(
        consumer_name=consumer,
        event_id=event_id,
        event_type="shipment.fact.custody_started",
        event_version=1,
        payload={"request_id": str(uuid4())},
        handler=calls.append,
    )


def test_a_first_delivery_runs_the_handler_and_acks() -> None:
    uow = build_store()
    service = inbox_service(uow)
    calls: list = []

    outcome = _deliver(service, calls, event_id=uuid4())

    assert outcome.handler_ran is True
    assert outcome.action is JetStreamConsumerAction.ACK
    assert len(calls) == 1


def test_a_redelivery_of_a_processed_event_does_not_run_again() -> None:
    """At-least-once delivery must not become at-least-once *effect*."""
    uow = build_store()
    service = inbox_service(uow)
    calls: list = []
    event_id = uuid4()

    _deliver(service, calls, event_id=event_id)
    outcome = _deliver(service, calls, event_id=event_id)

    assert len(calls) == 1
    assert outcome.handler_ran is False
    assert outcome.action is JetStreamConsumerAction.ACK
    assert outcome.reason == "terminal_processed_duplicate"


def test_two_consumers_each_get_their_own_chance_at_one_event() -> None:
    uow = build_store()
    service = inbox_service(uow)
    calls: list = []
    event_id = uuid4()

    _deliver(service, calls, event_id=event_id, consumer=CONSUMER)
    _deliver(service, calls, event_id=event_id, consumer=OTHER_CONSUMER)

    assert len(calls) == 2


def test_a_failed_handler_is_marked_for_retry() -> None:
    uow = build_store()
    service = inbox_service(uow)

    outcome = service.handle(
        consumer_name=CONSUMER,
        event_id=uuid4(),
        event_type="shipment.fact.custody_started",
        event_version=1,
        payload={},
        handler=_explode,
    )

    assert outcome.action is JetStreamConsumerAction.NAK
    assert outcome.record.status is InboxStatus.FAILED
    assert outcome.record.last_error_code == "RuntimeError"


def test_a_failed_event_is_retried_on_redelivery() -> None:
    uow = build_store()
    service = inbox_service(uow)
    event_id = uuid4()
    service.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="shipment.fact.custody_started",
        event_version=1,
        payload={},
        handler=_explode,
    )

    calls: list = []
    outcome = _deliver(service, calls, event_id=event_id)

    assert outcome.handler_ran is True
    assert len(calls) == 1
    assert outcome.record.status is InboxStatus.PROCESSED
    assert outcome.record.attempt_count == 2


def test_a_redelivery_inside_a_live_lease_is_deferred_not_rerun() -> None:
    """The one case where running again would genuinely double the effect."""
    uow = build_store()
    service = inbox_service(uow)
    event_id = uuid4()
    moment = datetime.now(tz=UTC)
    uow.begin()
    uow.inbox.insert(
        InboxRecord(
            inbox_id=uuid4(),
            consumer_name=CONSUMER,
            event_id=event_id,
            event_type="shipment.fact.custody_started",
            event_version=1,
            status=InboxStatus.PROCESSING,
            received_at=moment,
            attempt_count=1,
            processing_started_at=moment,
            processing_lease_until=moment + timedelta(seconds=30),
        )
    )
    uow.commit()

    calls: list = []
    outcome = _deliver(service, calls, event_id=event_id)

    assert outcome.action is JetStreamConsumerAction.DEFER
    assert outcome.handler_ran is False
    assert calls == []


def test_an_expired_lease_is_reclaimed() -> None:
    uow = build_store()
    service = inbox_service(uow)
    event_id = uuid4()
    moment = datetime.now(tz=UTC) - timedelta(minutes=5)
    uow.begin()
    uow.inbox.insert(
        InboxRecord(
            inbox_id=uuid4(),
            consumer_name=CONSUMER,
            event_id=event_id,
            event_type="shipment.fact.custody_started",
            event_version=1,
            status=InboxStatus.PROCESSING,
            received_at=moment,
            attempt_count=1,
            processing_started_at=moment,
            processing_lease_until=moment + timedelta(seconds=30),
        )
    )
    uow.commit()

    calls: list = []
    outcome = _deliver(service, calls, event_id=event_id)

    assert outcome.handler_ran is True
    assert outcome.reason == "expired_processing_lease_reclaim"
    assert len(calls) == 1


def test_a_quarantined_event_is_terminal_by_default() -> None:
    uow = build_store()
    service = inbox_service(uow)
    event_id = uuid4()
    uow.begin()
    uow.inbox.insert(
        InboxRecord(
            inbox_id=uuid4(),
            consumer_name=CONSUMER,
            event_id=event_id,
            event_type="shipment.fact.custody_started",
            event_version=1,
            status=InboxStatus.QUARANTINED,
            received_at=datetime.now(tz=UTC),
            attempt_count=5,
        )
    )
    uow.commit()

    calls: list = []
    outcome = _deliver(service, calls, event_id=event_id)

    assert outcome.action is JetStreamConsumerAction.ACK
    assert outcome.handler_ran is False
    assert calls == []


def test_a_quarantined_event_can_be_replayed_when_policy_says_so() -> None:
    uow = build_store()
    service = inbox_service(
        uow, quarantine_policy=QuarantineRedeliveryPolicy.REPLAY_RESET
    )
    event_id = uuid4()
    uow.begin()
    uow.inbox.insert(
        InboxRecord(
            inbox_id=uuid4(),
            consumer_name=CONSUMER,
            event_id=event_id,
            event_type="shipment.fact.custody_started",
            event_version=1,
            status=InboxStatus.QUARANTINED,
            received_at=datetime.now(tz=UTC),
            attempt_count=5,
        )
    )
    uow.commit()

    calls: list = []
    outcome = _deliver(service, calls, event_id=event_id)

    assert outcome.handler_ran is True
    assert len(calls) == 1


def test_a_handler_that_raises_does_not_lose_the_inbox_row() -> None:
    uow = build_store()
    service = inbox_service(uow)
    event_id = uuid4()

    service.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="shipment.fact.custody_started",
        event_version=1,
        payload={},
        handler=_explode,
    )

    uow.begin()
    stored = uow.inbox.find(CONSUMER, event_id)
    uow.commit()
    assert stored is not None
    assert stored.status is InboxStatus.FAILED


def _explode(_payload) -> None:
    msg = "handler blew up"
    raise RuntimeError(msg)


def test_the_explode_helper_actually_raises() -> None:
    with pytest.raises(RuntimeError):
        _explode({})
