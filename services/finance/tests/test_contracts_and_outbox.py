"""Finance event contracts and the transactional outbox.

The rule that matters most here is the last one: a failed transaction publishes nothing.
For money, an event that outlived its own rollback is worse than a lost event — a
consumer would credit a merchant for a settlement that never happened.

What crosses the bus and what does not is also deliberate. A payout destination is an
IBAN or a card reference; a rejection reason can name a person or an investigation.
Neither leaves Finance, and the schemas publish only whether they exist.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from finance_fixtures import (
    accountant,
    build_lab,
    cashier,
    collect_cash,
    iqd,
    operations,
    receipt,
)

from finance.domain.messaging import OutboxStatus
from finance.domain.value_objects import CodPaymentChannel, DepositMethod, PayoutMethod
from finance.infrastructure.contracts.envelopes import (
    build_cash_limit_breached_envelope,
    build_cod_settled_envelope,
    build_payout_decided_envelope,
)
from finance.infrastructure.contracts.registry import (
    FINANCE_FACT_CONTRACTS,
    EnvelopeContractValidationFailed,
    load_finance_fact_registry,
    validate_envelope,
)
from finance.infrastructure.nats.subjects import (
    ALLOWED_SUBJECTS,
    CONSUMED_SUBJECTS,
    expected_stream_for_subject,
    validate_subject_allowed,
)

# ------------------------------------------------------------------ the registry


@pytest.mark.parametrize(("event_type", "event_version"), FINANCE_FACT_CONTRACTS)
def test_every_declared_contract_resolves(event_type: str, event_version: int) -> None:
    loaded = load_finance_fact_registry(event_type, event_version)
    assert loaded.contract.producer == "finance"
    assert loaded.contract.subject in ALLOWED_SUBJECTS


def test_the_subject_allowlist_matches_the_registry() -> None:
    registered = {
        load_finance_fact_registry(t, v).contract.subject
        for t, v in FINANCE_FACT_CONTRACTS
    }
    assert registered == set(ALLOWED_SUBJECTS)


def test_every_subject_binds_to_the_finance_stream() -> None:
    for subject in ALLOWED_SUBJECTS:
        assert expected_stream_for_subject(subject) == "HUDHUD_FINANCE"


def test_finance_never_publishes_to_a_subject_it_consumes() -> None:
    """Delivery says what happened at the door; Finance says what it means (ADR-0012)."""
    assert frozenset() == ALLOWED_SUBJECTS & CONSUMED_SUBJECTS
    assert all(s.startswith("hudhud.delivery.") for s in CONSUMED_SUBJECTS)


def test_an_unlisted_subject_is_refused() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_subject_allowed("hudhud.finance.something.else.v1")


def test_a_malformed_envelope_is_refused_before_publication() -> None:
    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            {"event_type": "finance.fact.cod_settled"},
            event_type="finance.fact.cod_settled",
            event_version=1,
        )


# ------------------------------------------------------------------ the facts


def test_a_card_collection_settles_and_publishes_at_once() -> None:
    """PAY-01 — card money is HUDHUD's the moment it is approved."""
    lab = build_lab()
    lab.cod.record_collection(
        tracking_code="SHP-20260915-700001",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.POS_CARD,
        goods_amount=iqd(50_000),
        delivery_fee=iqd(2_500),
        driver_principal_id=lab.driver_id,
    )
    published = [r.event_type for r in lab.uow.outbox.list_pending()]
    assert "finance.fact.cod_settled" in published


def test_a_cash_collection_publishes_nothing_until_it_reaches_a_cashier() -> None:
    """PAY-01 — cash counts as paid only at the hub cashier, so the fact waits."""
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    assert lab.uow.outbox.list_pending() == ()


def test_a_hub_deposit_publishes_the_settlement_it_caused() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(50_000),
        reference="HUB-PUB-1",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    published = [r.event_type for r in lab.uow.outbox.list_pending()]
    assert published == ["finance.fact.cod_settled"]


def test_an_exchange_deposit_publishes_no_settlement() -> None:
    """PAY-04 — it settles collected cash and confirms no original payment."""
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(50_000),
        reference="EX-PUB-1",
        receipt=receipt(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=accountant())
    assert lab.uow.outbox.list_pending() == ()


def test_a_settled_cash_fact_names_the_deposit_that_settled_it() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(50_000),
        reference="HUB-PUB-2",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    payload = lab.uow.outbox.list_pending()[0].payload_json["payload"]
    assert payload["settling_deposit_id"] == str(deposit.deposit_id)
    assert payload["channel"] == "CASH"


def test_a_breach_is_published_when_a_collection_reaches_the_limit() -> None:
    """DRV-A06, OPS-04."""
    lab = build_lab(cash_limit=50_000)
    collect_cash(lab, goods=50_000, fee=0)
    published = [r.event_type for r in lab.uow.outbox.list_pending()]
    assert "finance.fact.cash_limit_breached" in published


def test_no_breach_is_published_below_the_limit() -> None:
    """A fact that fired early would stop work reaching a driver who is fine."""
    lab = build_lab(cash_limit=500_000)
    collect_cash(lab, goods=10_000, fee=0)
    published = [r.event_type for r in lab.uow.outbox.list_pending()]
    assert "finance.fact.cash_limit_breached" not in published


def test_the_builder_refuses_to_announce_a_breach_that_has_not_happened() -> None:
    with pytest.raises(ValueError, match="under their limit"):
        build_cash_limit_breached_envelope(
            driver_principal_id=uuid4(),
            held=iqd(10),
            limit=iqd(100),
            utilisation_percent=10,
            observed_at=datetime.now(tz=UTC),
            aggregate_version=1,
            event_id=uuid4(),
            correlation_id=uuid4(),
        )


def test_a_payout_request_publishes_its_fact() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        destination_reference="IQ00BANK0001",
    )
    published = [r.event_type for r in lab.uow.outbox.list_pending()]
    assert "finance.fact.payout_requested" in published


def test_a_payout_decision_publishes_its_fact() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        destination_reference="IQ00BANK0001",
    )
    lab.settlement.approve_payout(payout_id=payout.payout_id, actor=operations())
    published = [r.event_type for r in lab.uow.outbox.list_pending()]
    assert "finance.fact.payout_decided" in published


def test_the_builder_refuses_to_call_an_undecided_payout_decided() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        destination_reference="IQ00BANK0001",
    )
    with pytest.raises(ValueError, match="is not a decision"):
        build_payout_decided_envelope(
            payout=payout,
            aggregate_version=1,
            event_id=uuid4(),
            correlation_id=uuid4(),
        )


def test_the_builder_refuses_to_call_an_unsettled_collection_settled() -> None:
    """The one mistake that would pay a merchant for cash in a driver's pocket."""
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    unsettled = lab.uow.collections.list_unsettled_for_driver(lab.driver_id)[0]
    with pytest.raises(ValueError, match="unsettled collection"):
        build_cod_settled_envelope(
            collection=unsettled,
            aggregate_version=1,
            event_id=uuid4(),
            correlation_id=uuid4(),
        )


# --------------------------------------------- a failed transaction publishes nothing


def test_every_published_fact_belongs_to_a_change_that_committed() -> None:
    """Walks the outbox and checks each fact against the state it claims."""
    lab = build_lab(cash_limit=200_000)
    collect_cash(lab, goods=50_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(50_000),
        reference="HUB-COMMIT",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    for record in lab.uow.outbox.list_pending():
        if record.event_type != "finance.fact.cod_settled":
            continue
        code = record.payload_json["payload"]["tracking_code"]
        assert lab.cod.collection_for(tracking_code=code).is_paid is True


def test_a_fact_shares_the_transaction_with_the_change_that_caused_it() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    before = lab.uow.commits
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(50_000),
        reference="HUB-ATOMIC",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    after_submit = lab.uow.commits
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    # One commit for the confirmation, its posting and the settlement fact together.
    assert lab.uow.commits == after_submit + 1
    assert after_submit > before


# ------------------------------------------------------------------ no leakage


def test_no_published_fact_carries_a_payout_destination() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        destination_reference="IQ98NBIQ1234567890",
    )
    lab.settlement.reject_payout(
        payout_id=payout.payout_id,
        actor=operations(),
        reason="the account belongs to a third party",
    )
    for record in lab.uow.outbox.list_pending():
        blob = json.dumps(record.payload_json)
        assert "IQ98NBIQ1234567890" not in blob
        assert "third party" not in blob


def test_a_rejection_fact_says_that_there_is_a_reason_not_what_it_is() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        destination_reference="IQ00BANK0001",
    )
    lab.settlement.reject_payout(
        payout_id=payout.payout_id, actor=operations(), reason="under investigation"
    )
    decided = next(
        r
        for r in lab.uow.outbox.list_pending()
        if r.event_type == "finance.fact.payout_decided"
    )
    assert decided.payload_json["payload"]["has_rejection_reason"] is True
    assert "investigation" not in json.dumps(decided.payload_json)


def test_every_fact_declares_it_carries_no_pii() -> None:
    lab = build_lab(cash_limit=60_000)
    collect_cash(lab, goods=60_000, fee=0)
    lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.IN_PERSON_AT_HUB,
        amount=iqd(10_000),
    )
    records = lab.uow.outbox.list_pending()
    assert records
    for record in records:
        assert record.payload_json["pii_present"] is False


def test_money_crosses_the_bus_as_integer_minor_units() -> None:
    lab = build_lab()
    lab.cod.record_collection(
        tracking_code="SHP-20260915-700009",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.POS_CARD,
        goods_amount=iqd(50_000),
        delivery_fee=iqd(2_500),
        driver_principal_id=lab.driver_id,
    )
    for record in lab.uow.outbox.list_pending():
        for key, value in record.payload_json["payload"].items():
            if key.endswith("minor_units") and value is not None:
                assert isinstance(value, int), key
                assert not isinstance(value, float), key


# ------------------------------------------------------------ the outbox itself


def test_records_carry_a_retry_budget_and_start_unpublished() -> None:
    lab = build_lab()
    lab.cod.record_collection(
        tracking_code="SHP-20260915-700010",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.ONLINE,
        goods_amount=iqd(10_000),
        delivery_fee=iqd(0),
    )
    record = lab.uow.outbox.list_pending()[0]
    assert record.status is OutboxStatus.PENDING
    assert record.attempt_count == 0
    assert record.max_attempts == 5
    assert record.published_at is None


def test_two_facts_never_share_an_aggregate_version() -> None:
    """The ordering key consumers rely on has to be unique per aggregate."""
    lab = build_lab(cash_limit=100_000)
    collect_cash(lab, goods=100_000, fee=0)
    collect_cash(lab, goods=50_000, fee=0)
    by_aggregate: dict = {}
    for record in lab.uow.outbox.list_pending():
        key = (record.aggregate_id, record.aggregate_version)
        assert key not in by_aggregate, key
        by_aggregate[key] = record.event_type
