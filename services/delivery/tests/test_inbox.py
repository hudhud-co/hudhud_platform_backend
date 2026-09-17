"""Idempotent consumption (ADR-0008).

JetStream is at-least-once, so the same message will arrive twice. What matters is that
the handler runs at most once per ``(consumer, event_id)`` and that the answer given back
to JetStream matches how far the previous attempt got.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from messaging_conformance.enums import JetStreamConsumerAction

from delivery.application.inbox_service import InboxService
from delivery.domain.messaging import InboxStatus
from delivery.infrastructure.memory import InMemoryUnitOfWork

CONSUMER = "delivery-assignment-consumer"


def build() -> tuple[InboxService, InMemoryUnitOfWork]:
    uow = InMemoryUnitOfWork()
    return InboxService(uow), uow


def handle(service: InboxService, event_id, calls: list, *, fail: bool = False):
    def handler(payload: dict) -> None:
        calls.append(payload)
        if fail:
            msg = "handler exploded"
            raise RuntimeError(msg)

    return service.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="hub.fact.assigned_for_last_mile",
        event_version=1,
        payload={"tracking_code": "SHP-20260915-000001"},
        handler=handler,
    )


def test_a_first_delivery_runs_the_handler() -> None:
    service, uow = build()
    calls: list = []
    outcome = handle(service, uuid4(), calls)
    assert outcome.handler_ran is True
    assert outcome.action is JetStreamConsumerAction.ACK
    assert len(calls) == 1


def test_a_redelivery_does_not_run_the_handler_again() -> None:
    service, _ = build()
    event_id = uuid4()
    calls: list = []
    handle(service, event_id, calls)
    outcome = handle(service, event_id, calls)
    assert outcome.handler_ran is False
    assert outcome.action is JetStreamConsumerAction.ACK
    assert len(calls) == 1


def test_the_same_event_on_another_consumer_is_a_different_record() -> None:
    """Two consumers are two independent readers of the same fact."""
    uow = InMemoryUnitOfWork()
    first = InboxService(uow)
    event_id = uuid4()
    calls: list = []
    handle(first, event_id, calls)

    def handler(payload: dict) -> None:
        calls.append(payload)

    outcome = first.handle(
        consumer_name="another-consumer",
        event_id=event_id,
        event_type="hub.fact.assigned_for_last_mile",
        event_version=1,
        payload={},
        handler=handler,
    )
    assert outcome.handler_ran is True
    assert len(calls) == 2


def test_a_failed_handler_leaves_the_record_failed() -> None:
    service, uow = build()
    event_id = uuid4()
    calls: list = []
    outcome = handle(service, event_id, calls, fail=True)
    assert outcome.handler_ran is True
    assert outcome.action is not JetStreamConsumerAction.ACK
    record = uow.inbox.find(CONSUMER, event_id)
    assert record.status is not InboxStatus.PROCESSED


def test_a_failed_event_can_be_retried() -> None:
    service, _ = build()
    event_id = uuid4()
    calls: list = []
    handle(service, event_id, calls, fail=True)
    outcome = handle(service, event_id, calls)
    assert outcome.handler_ran is True
    assert len(calls) == 2


def test_a_redelivery_inside_the_lease_is_deferred_not_rerun() -> None:
    """The one case where running again would produce a second effect."""
    uow = InMemoryUnitOfWork()
    service = InboxService(uow, lease_seconds=300)
    event_id = uuid4()
    calls: list = []

    def reentrant(payload: dict) -> None:
        calls.append(payload)

    # Leave a record parked in PROCESSING, as a crashed attempt would.
    service.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="hub.fact.assigned_for_last_mile",
        event_version=1,
        payload={},
        handler=reentrant,
    )
    record = uow.inbox.find(CONSUMER, event_id)
    record.status = InboxStatus.PROCESSING
    record.processing_lease_until = datetime.now(tz=UTC) + timedelta(seconds=300)
    uow.inbox.save(record)

    outcome = service.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="hub.fact.assigned_for_last_mile",
        event_version=1,
        payload={},
        handler=reentrant,
    )
    assert outcome.handler_ran is False
    assert outcome.action is JetStreamConsumerAction.DEFER
    assert len(calls) == 1


def test_the_record_keeps_what_arrived() -> None:
    service, uow = build()
    event_id = uuid4()
    handle(service, event_id, [])
    record = uow.inbox.find(CONSUMER, event_id)
    assert record.event_type == "hub.fact.assigned_for_last_mile"
    assert record.event_version == 1
    assert record.attempt_count >= 1


def test_the_inbox_key_is_the_consumer_and_the_event() -> None:
    uow = InMemoryUnitOfWork()
    service = InboxService(uow)
    event_id = uuid4()
    handle(service, event_id, [])
    assert uow.inbox.find(CONSUMER, event_id) is not None
    assert uow.inbox.find("someone-else", event_id) is None


def test_inserting_the_same_key_twice_is_refused_by_the_store() -> None:
    """The double stands in for the unique index the real table carries."""
    uow = InMemoryUnitOfWork()
    service = InboxService(uow)
    event_id = uuid4()
    handle(service, event_id, [])
    record = uow.inbox.find(CONSUMER, event_id)
    with pytest.raises(ValueError, match="duplicate inbox record"):
        uow.inbox.insert(record)
