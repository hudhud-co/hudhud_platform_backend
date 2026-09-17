"""Idempotent consumption, where a second run means the money moves twice.

JetStream is at-least-once, so the same `delivery.fact.cod_collected` will arrive again.
Everywhere else in the platform a duplicated handler is an annoyance; here it is a double
credit to a merchant and a double charge against a driver's custody, so these tests drive
the real COD handler through real redeliveries and check the ledger afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from finance_fixtures import build_lab, iqd
from messaging_conformance.enums import JetStreamConsumerAction

from finance.application.inbox_service import InboxService
from finance.domain.ledger import balance_of, driver_custody, merchant_payable
from finance.domain.messaging import InboxStatus
from finance.domain.value_objects import CodPaymentChannel

CONSUMER = "finance-cod-consumer"


def collected_payload(tracking_code: str, driver_id, merchant_id, amount: int) -> dict:
    """What Delivery publishes when money changes hands at the door."""
    return {
        "tracking_code": tracking_code,
        "collecting_driver_id": str(driver_id),
        "merchant_id": str(merchant_id),
        "amount_minor_units": amount,
        "currency": "IQD",
        "method": "CASH",
        "enters_driver_cash_custody": True,
    }


def build():
    lab = build_lab(cash_limit=10_000_000)
    return lab, InboxService(lab.uow)


def post_cod(lab, payload: dict) -> None:
    """The real handler: record the collection Delivery told us about.

    Calls the ``_within_transaction`` variant because the inbox already holds one — the
    deduplication record and this posting have to commit together or the dedup guarantee
    is worthless.
    """
    lab.cod.record_collection_within_transaction(
        tracking_code=payload["tracking_code"],
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.CASH,
        goods_amount=iqd(payload["amount_minor_units"]),
        delivery_fee=iqd(0),
        driver_principal_id=lab.driver_id,
        idempotency_key=f"cod:{payload['tracking_code']}",
    )


def deliver(lab, inbox, event_id, payload):
    return inbox.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="delivery.fact.cod_collected",
        event_version=1,
        payload=payload,
        handler=lambda p: post_cod(lab, p),
    )


# --------------------------------------------------- the ordinary path


def test_a_first_delivery_posts_the_money() -> None:
    lab, inbox = build()
    payload = collected_payload(
        "SHP-20260915-600001", lab.driver_id, lab.merchant_id, 60_000
    )
    outcome = deliver(lab, inbox, uuid4(), payload)
    assert outcome.handler_ran is True
    assert outcome.action is JetStreamConsumerAction.ACK
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(60_000)


# ------------------------------- a duplicate delivery has no financial effect


def test_a_redelivery_posts_nothing_a_second_time() -> None:
    """The invariant this whole file exists for."""
    lab, inbox = build()
    event_id = uuid4()
    payload = collected_payload(
        "SHP-20260915-600002", lab.driver_id, lab.merchant_id, 75_000
    )
    deliver(lab, inbox, event_id, payload)
    repeat = deliver(lab, inbox, event_id, payload)

    assert repeat.handler_ran is False
    assert repeat.action is JetStreamConsumerAction.ACK
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(75_000)
    assert len(lab.uow.ledger.all_entries) == 1


def test_ten_redeliveries_still_post_once() -> None:
    lab, inbox = build()
    event_id = uuid4()
    payload = collected_payload(
        "SHP-20260915-600003", lab.driver_id, lab.merchant_id, 30_000
    )
    for _ in range(10):
        deliver(lab, inbox, event_id, payload)
    payable = merchant_payable(lab.merchant_id)
    assert balance_of(
        payable, lab.uow.ledger.entries_for_account(payable)
    ).amount == iqd(30_000)
    assert len(lab.uow.ledger.all_entries) == 1


def test_a_redelivery_under_a_new_event_id_is_still_refused_by_the_ledger() -> None:
    """Belt and braces: the inbox catches the replay, the idempotency key catches the
    re-publish, and the tracking code catches everything else."""
    lab, inbox = build()
    payload = collected_payload(
        "SHP-20260915-600004", lab.driver_id, lab.merchant_id, 20_000
    )
    deliver(lab, inbox, uuid4(), payload)
    # A different event id means the inbox lets it through — the ledger must not.
    second = deliver(lab, inbox, uuid4(), payload)
    assert second.handler_ran is True
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(20_000)
    assert len(lab.uow.ledger.all_entries) == 1


def test_two_different_parcels_both_post() -> None:
    """Deduplication must not be so eager that real work is dropped."""
    lab, inbox = build()
    for code, amount in (
        ("SHP-20260915-600005", 10_000),
        ("SHP-20260915-600006", 15_000),
    ):
        deliver(
            lab,
            inbox,
            uuid4(),
            collected_payload(code, lab.driver_id, lab.merchant_id, amount),
        )
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(25_000)


def test_the_same_event_on_a_second_consumer_is_a_different_reader() -> None:
    lab, inbox = build()
    event_id = uuid4()
    payload = collected_payload(
        "SHP-20260915-600007", lab.driver_id, lab.merchant_id, 5_000
    )
    deliver(lab, inbox, event_id, payload)
    outcome = inbox.handle(
        consumer_name="finance-audit-consumer",
        event_id=event_id,
        event_type="delivery.fact.cod_collected",
        event_version=1,
        payload=payload,
        handler=lambda _p: None,
    )
    assert outcome.handler_ran is True
    assert lab.uow.inbox.find(CONSUMER, event_id) is not None
    assert lab.uow.inbox.find("finance-audit-consumer", event_id) is not None


# ------------------------------------------------------------ failures


def test_a_handler_that_raised_leaves_the_record_unprocessed() -> None:
    lab, inbox = build()
    event_id = uuid4()

    def explode(_payload: dict) -> None:
        msg = "downstream is down"
        raise RuntimeError(msg)

    outcome = inbox.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="delivery.fact.cod_collected",
        event_version=1,
        payload={},
        handler=explode,
    )
    assert outcome.action is not JetStreamConsumerAction.ACK
    assert lab.uow.inbox.find(CONSUMER, event_id).status is not InboxStatus.PROCESSED


def test_a_failed_event_can_be_retried_and_then_posts_once() -> None:
    lab, inbox = build()
    event_id = uuid4()
    payload = collected_payload(
        "SHP-20260915-600008", lab.driver_id, lab.merchant_id, 12_000
    )
    attempts = {"n": 0}

    def flaky(p: dict) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            msg = "transient"
            raise RuntimeError(msg)
        post_cod(lab, p)

    inbox.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="delivery.fact.cod_collected",
        event_version=1,
        payload=payload,
        handler=flaky,
    )
    inbox.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="delivery.fact.cod_collected",
        event_version=1,
        payload=payload,
        handler=flaky,
    )
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(12_000)
    assert len(lab.uow.ledger.all_entries) == 1


def test_a_redelivery_inside_the_lease_is_deferred_rather_than_run() -> None:
    """The one case where running again really would post the money twice."""
    lab = build_lab()
    inbox = InboxService(lab.uow, lease_seconds=300)
    event_id = uuid4()
    calls: list = []

    inbox.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="delivery.fact.cod_collected",
        event_version=1,
        payload={},
        handler=lambda _p: calls.append(1),
    )
    record = lab.uow.inbox.find(CONSUMER, event_id)
    record.status = InboxStatus.PROCESSING
    record.processing_lease_until = datetime.now(tz=UTC) + timedelta(seconds=300)
    lab.uow.inbox.save(record)

    outcome = inbox.handle(
        consumer_name=CONSUMER,
        event_id=event_id,
        event_type="delivery.fact.cod_collected",
        event_version=1,
        payload={},
        handler=lambda _p: calls.append(1),
    )
    assert outcome.handler_ran is False
    assert outcome.action is JetStreamConsumerAction.DEFER
    assert len(calls) == 1


def test_the_inbox_key_is_the_consumer_and_the_event() -> None:
    lab, inbox = build()
    event_id = uuid4()
    deliver(
        lab,
        inbox,
        event_id,
        collected_payload(
            "SHP-20260915-600009", lab.driver_id, lab.merchant_id, 1_000
        ),
    )
    assert lab.uow.inbox.find(CONSUMER, event_id) is not None
    assert lab.uow.inbox.find("someone-else", event_id) is None
    with pytest.raises(ValueError, match="duplicate inbox record"):
        lab.uow.inbox.insert(lab.uow.inbox.find(CONSUMER, event_id))
