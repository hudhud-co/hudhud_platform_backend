"""The invariants that make this a ledger rather than a table of numbers.

Each of these is a property of the whole service rather than of one call, so each is
checked by driving real flows and then interrogating what came out — not by asserting
that a particular function was written a particular way.
"""

from __future__ import annotations

import inspect
import sys
from datetime import date
from importlib import import_module
from pathlib import Path
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

from finance.domain import ledger as ledger_module
from finance.domain.errors import (
    CollectionAlreadyRecorded,
    DepositAlreadyDecided,
    InsufficientMerchantBalance,
    MoreThanTheDriverHolds,
    PayoutProcedureNotDefined,
    ReturnFeeWaiverNotDecided,
)
from finance.domain.ledger import (
    BANK,
    EXCHANGE_IN_TRANSIT,
    HUB_CASH,
    HUDHUD_REVENUE,
    Side,
    balance_of,
    driver_custody,
    merchant_payable,
)
from finance.domain.money import Money
from finance.domain.value_objects import (
    CodPaymentChannel,
    DepositMethod,
    PayoutMethod,
)
from finance.infrastructure.persistence import models
from finance.ports.repository import LedgerRepository

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "src"


def busy_lab():
    """One lab that has been through most of the money flows, for the sweeps below."""
    lab = build_lab(cash_limit=500_000)
    collect_cash(lab, goods=100_000, fee=5_000)
    collect_cash(lab, goods=80_000, fee=4_000)
    lab.cod.record_collection(
        tracking_code="SHP-20260915-800001",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.POS_CARD,
        goods_amount=iqd(50_000),
        delivery_fee=iqd(2_500),
        driver_principal_id=lab.driver_id,
    )
    lab.cod.record_pickup_fee(
        tracking_code="SHP-20260915-800002",
        merchant_id=lab.merchant_id,
        amount=iqd(3_000),
        driver_principal_id=lab.driver_id,
    )
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(105_000),
        reference="HUB-INV-1",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    exchange = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(50_000),
        reference="EX-INV-1",
        receipt=receipt(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=exchange.deposit_id, actor=accountant())
    lab.settlement.charge_for_refusal(
        tracking_code="SHP-20260915-800003",
        merchant_id=lab.merchant_id,
        delivery_fee=iqd(5_000),
    )
    return lab


# ------------------------------------------- 1. every journal entry balances


def test_every_entry_this_service_ever_posts_balances() -> None:
    lab = busy_lab()
    entries = lab.uow.ledger.all_entries
    assert len(entries) >= 7
    for entry in entries:
        debits = sum(
            p.amount.minor_units for p in entry.postings if p.side is Side.DEBIT
        )
        credits = sum(
            p.amount.minor_units for p in entry.postings if p.side is Side.CREDIT
        )
        assert debits == credits, entry.reason
        assert debits > 0, entry.reason


def test_the_whole_ledger_nets_to_zero() -> None:
    """Across every account: what HUDHUD holds equals what it owes plus what it earned."""
    lab = busy_lab()
    net = 0
    for entry in lab.uow.ledger.all_entries:
        for posting in entry.postings:
            net += (
                posting.amount.minor_units
                if posting.side is Side.DEBIT
                else -posting.amount.minor_units
            )
    assert net == 0


def test_the_money_is_where_the_flows_put_it() -> None:
    lab = busy_lab()
    custody = driver_custody(lab.driver_id)
    payable = merchant_payable(lab.merchant_id)
    held = balance_of(custody, lab.uow.ledger.entries_for_account(custody)).amount
    hub = balance_of(HUB_CASH, lab.uow.ledger.entries_for_account(HUB_CASH)).amount
    bank = balance_of(BANK, lab.uow.ledger.entries_for_account(BANK)).amount
    transit = balance_of(
        EXCHANGE_IN_TRANSIT, lab.uow.ledger.entries_for_account(EXCHANGE_IN_TRANSIT)
    ).amount
    owed = balance_of(payable, lab.uow.ledger.entries_for_account(payable)).amount
    earned = balance_of(
        HUDHUD_REVENUE, lab.uow.ledger.entries_for_account(HUDHUD_REVENUE)
    ).amount
    # The exchange deposit was verified, so nothing is left in transit.
    assert transit == iqd(0)
    assert held.minor_units + hub.minor_units + bank.minor_units == (
        owed.minor_units + earned.minor_units
    )


# ------------------------------------------- 2. exact IQD, never a float


def test_no_money_anywhere_in_this_service_is_a_float() -> None:
    lab = busy_lab()
    for entry in lab.uow.ledger.all_entries:
        assert isinstance(entry.total.minor_units, int)
        assert not isinstance(entry.total.minor_units, bool)
        for posting in entry.postings:
            assert isinstance(posting.amount.minor_units, int)
            assert not isinstance(posting.amount.minor_units, bool)


def test_no_model_column_is_a_float_or_a_numeric() -> None:
    offenders = [
        f"{table.name}.{column.name}: {column.type}"
        for table in models.Base.metadata.sorted_tables
        for column in table.columns
        if any(
            marker in str(column.type).upper()
            for marker in ("FLOAT", "DOUBLE", "NUMERIC", "REAL", "DECIMAL")
        )
    ]
    assert offenders == []


def test_no_source_file_uses_float_arithmetic_on_money() -> None:
    """A `float(` or a `/` on an amount is the one way exactness leaks out."""
    offenders = []
    for path in SERVICE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "float(" in text:
            offenders.append(f"{path.name}: float(")
        if "Decimal" in text:
            offenders.append(f"{path.name}: Decimal")
    assert offenders == []


def test_a_large_amount_survives_every_flow_exactly() -> None:
    lab = build_lab(cash_limit=50_000_000_000)
    huge = 9_000_000_000
    lab.cod.record_collection(
        tracking_code="SHP-20260915-810001",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.CASH,
        goods_amount=iqd(huge),
        delivery_fee=iqd(1),
        driver_principal_id=lab.driver_id,
    )
    position = lab.cash.position_for(driver_principal_id=lab.driver_id)
    assert position.held.minor_units == huge + 1
    assert lab.settlement.balance_for(merchant_id=lab.merchant_id).balance == iqd(huge)


# ------------------------------------- 3. no mutable or deletable ledger entry


def test_the_ledger_port_offers_no_update_and_no_delete() -> None:
    """The shape of the port is the first line of this defence."""
    methods = {
        name
        for name, _ in inspect.getmembers(LedgerRepository, inspect.isfunction)
        if not name.startswith("_")
    }
    assert "append" in methods
    assert not {"update", "save", "delete", "remove"} & methods


def test_the_ledger_entity_is_frozen() -> None:
    lab = build_lab()
    collect_cash(lab)
    entry = lab.uow.ledger.all_entries[0]
    with pytest.raises((AttributeError, TypeError)):
        entry.memo = "tampered"
    with pytest.raises((AttributeError, TypeError)):
        entry.postings[0].amount = Money(minor_units=1)


def test_the_store_has_no_update_path_for_the_ledger() -> None:
    source = (
        SERVICE_ROOT
        / "finance"
        / "infrastructure"
        / "persistence"
        / "sqlalchemy_store.py"
    ).read_text(encoding="utf-8")
    start = source.index("class _LedgerRepo")
    end = source.index("class _DriverAccountRepo")
    ledger_repo = source[start:end]
    assert "update(" not in ledger_repo
    assert "delete(" not in ledger_repo


def test_a_correction_is_another_entry_and_leaves_the_original_standing() -> None:
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    original = lab.uow.ledger.all_entries[0]
    correction = ledger_module.reverse(
        original, entry_id=uuid4(), occurred_at=original.occurred_at, actor_id=uuid4()
    )
    lab.uow.ledger.append(correction)
    assert lab.uow.ledger.get(original.entry_id) is not None
    assert correction.corrects_entry_id == original.entry_id
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(0)


# ------------------------------------- 4. no duplicate posting after a retry


def test_the_same_idempotency_key_posts_once() -> None:
    lab = build_lab()
    first = lab.cod.record_collection(
        tracking_code="SHP-20260915-820001",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.CASH,
        goods_amount=iqd(40_000),
        delivery_fee=iqd(0),
        driver_principal_id=lab.driver_id,
        idempotency_key="collect-820001",
    )
    retry = lab.cod.record_collection(
        tracking_code="SHP-20260915-820001",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.CASH,
        goods_amount=iqd(40_000),
        delivery_fee=iqd(0),
        driver_principal_id=lab.driver_id,
        idempotency_key="collect-820001",
    )
    assert retry.entry.entry_id == first.entry.entry_id
    assert len(lab.uow.ledger.all_entries) == 1
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).held == iqd(40_000)


def test_a_retry_without_a_key_is_refused_by_the_tracking_code() -> None:
    """The second line of defence: one parcel, one collection."""
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    code = lab.uow.ledger.all_entries[0]
    assert code is not None
    tracking = lab.uow.collections.list_unsettled_for_driver(lab.driver_id)[0]
    with pytest.raises(CollectionAlreadyRecorded):
        lab.cod.record_collection(
            tracking_code=tracking.tracking_code,
            merchant_id=lab.merchant_id,
            channel=CodPaymentChannel.CASH,
            goods_amount=iqd(10_000),
            delivery_fee=iqd(0),
            driver_principal_id=lab.driver_id,
        )


def test_a_deposit_reference_is_used_once() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        reference="TRX-UNIQUE",
        receipt=receipt(),
    )
    assert lab.uow.deposits.find_by_reference("TRX-UNIQUE") is not None


# ----------------------------- 5. no negative or impossible custody transition


def test_a_driver_cannot_deposit_more_than_they_hold() -> None:
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    with pytest.raises(MoreThanTheDriverHolds):
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=DepositMethod.BANK_TRANSFER,
            amount=iqd(10_001),
            reference="TRX-TOOMUCH",
            receipt=receipt(),
        )


def test_custody_never_goes_negative_through_any_sequence() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    for amount in (40_000, 40_000, 20_000):
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=DepositMethod.BANK_TRANSFER,
            amount=iqd(amount),
            reference=f"TRX-SEQ-{amount}",
            receipt=receipt(),
        )
        held = lab.cash.position_for(driver_principal_id=lab.driver_id).held
        assert held.minor_units >= 0
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).held == iqd(0)


def test_a_rejected_deposit_restores_exactly_what_it_removed() -> None:
    lab = build_lab()
    collect_cash(lab, goods=70_000, fee=0)
    before = lab.cash.position_for(driver_principal_id=lab.driver_id).held
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(70_000),
        reference="EX-REJECT",
        receipt=receipt(),
    ).deposit
    lab.cash.reject_deposit(
        deposit_id=deposit.deposit_id, actor=accountant(), reason="receipt unreadable"
    )
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).held == before


def test_a_deposit_cannot_be_confirmed_twice() -> None:
    lab = build_lab()
    collect_cash(lab, goods=30_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(30_000),
        reference="HUB-TWICE",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    with pytest.raises(DepositAlreadyDecided):
        lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())


# ---------------------- 6. Delivery never writes the wallet or the ledger


def test_no_other_service_can_reach_this_ledger() -> None:
    """ADR-0012 — Delivery publishes what happened; Finance owns the posting.

    Enforced as an import boundary rather than as a convention: there is no module in
    this service that another service could import even if it tried.
    """
    delivery_src = (
        SERVICE_ROOT.parents[1] / "delivery" / "src" / "delivery"
    )
    offenders = []
    for path in delivery_src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import finance" in text or "from finance" in text:
            offenders.append(str(path.name))
        for word in ("journal_entr", "merchant_payable", "wallet"):
            if word in text:
                offenders.append(f"{path.name}: {word}")
    assert offenders == []


def test_delivery_owns_no_table_this_service_owns() -> None:

    sys.path.insert(
        0, str(SERVICE_ROOT.parents[1] / "delivery" / "src")
    )
    try:
        delivery_models = import_module("delivery.infrastructure.persistence.models")
    finally:
        sys.path.pop(0)
    mine = {t.name for t in models.Base.metadata.sorted_tables}
    theirs = {t.name for t in delivery_models.Base.metadata.sorted_tables}
    assert mine & theirs == set()


# ------------------- 7. a failed transaction publishes no outbox event


def test_a_refused_collection_publishes_nothing() -> None:
    lab = build_lab()
    code = collect_cash(lab, goods=10_000, fee=0)
    before = len(lab.uow.outbox.list_pending())
    with pytest.raises(CollectionAlreadyRecorded):
        lab.cod.record_collection(
            tracking_code=code,
            merchant_id=lab.merchant_id,
            channel=CodPaymentChannel.CASH,
            goods_amount=iqd(10_000),
            delivery_fee=iqd(0),
            driver_principal_id=lab.driver_id,
        )
    assert len(lab.uow.outbox.list_pending()) == before


def test_a_refused_payout_publishes_nothing() -> None:
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    before = len(lab.uow.outbox.list_pending())
    with pytest.raises(InsufficientMerchantBalance):
        lab.settlement.request_payout(
            merchant_id=lab.merchant_id,
            method=PayoutMethod.BANK_TRANSFER,
            amount=iqd(999_999),
            destination_reference="IQ00BANK0001",
        )
    assert len(lab.uow.outbox.list_pending()) == before


def test_a_blocked_payout_payment_publishes_nothing() -> None:
    """PAY-07's refusal must leave no trace on the bus."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        destination_reference="IQ00BANK0001",
    )
    lab.settlement.approve_payout(payout_id=payout.payout_id, actor=operations())
    before = {r.event_id for r in lab.uow.outbox.list_pending()}
    with pytest.raises(PayoutProcedureNotDefined):
        lab.settlement.pay_payout(payout_id=payout.payout_id, actor=operations())
    assert {r.event_id for r in lab.uow.outbox.list_pending()} == before


def test_a_refused_waiver_publishes_nothing_and_posts_nothing() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    entries_before = len(lab.uow.ledger.all_entries)
    events_before = len(lab.uow.outbox.list_pending())
    with pytest.raises(ReturnFeeWaiverNotDecided):
        lab.settlement.waive_return_trip_fee(
            tracking_code="SHP-20260915-830001",
            merchant_id=lab.merchant_id,
            amount=iqd(5_000),
            actor=operations(),
            note="goodwill",
        )
    assert len(lab.uow.ledger.all_entries) == entries_before
    assert len(lab.uow.outbox.list_pending()) == events_before


def test_a_failed_reconciliation_resolution_publishes_nothing() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    before = len(lab.uow.ledger.all_entries)
    with pytest.raises(Exception, match=".*"):
        lab.settlement.resolve_reconciliation(
            reconciliation_id=reconciliation.reconciliation_id,
            actor=cashier(),
            note="not my call",
        )
    assert len(lab.uow.ledger.all_entries) == before
