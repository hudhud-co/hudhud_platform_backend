"""The merchant's side: balance, payouts, the refusal charge, and reconciliation.

The two v6.3 Open Items live here, and the point of these tests is to pin down exactly
how much is blocked by each — because "blocked" in the accounting must mean one refused
operation, not a missing feature.

* **PAY-07** blocks paying a payout out. Requesting, approving and rejecting all work.
* **PAY-08** blocks *waiving* the return-trip fee. Charging it works, because v6.3 p.38
  and p.39 settle that part.
"""

from __future__ import annotations

import inspect
from datetime import date
from uuid import uuid4

import pytest
from finance_fixtures import (
    TEST_RETURN_TRIP_FEE,
    accountant,
    build_lab,
    cashier,
    collect_cash,
    driver_actor,
    iqd,
    next_tracking_code,
    operations,
    receipt,
)

from finance.domain.errors import (
    InsufficientMerchantBalance,
    OnlyOperationsApprovesAPayout,
    OnlyOperationsResolvesAMismatch,
    PayoutDestinationRequired,
    PayoutProcedureNotDefined,
    PayoutsBlockedForThisMerchant,
    PayoutTransitionNotAllowed,
    ReconciliationAlreadyResolved,
    ResolutionNoteRequired,
    ReturnFeeWaiverNotDecided,
    TariffNotConfigured,
    UnresolvedReconciliationBlocksPayout,
)
from finance.domain.ledger import (
    HUDHUD_REVENUE,
    balance_of,
    driver_custody,
    merchant_payable,
)
from finance.domain.value_objects import (
    CodPaymentChannel,
    DepositMethod,
    PayoutMethod,
    PayoutStatus,
    ReconciliationOutcome,
    ReconciliationStatus,
)

# ------------------------------------------------------------------ PAY-05


def test_a_merchant_balance_is_the_goods_and_not_the_fee() -> None:
    """A single credit would overstate what the merchant is owed by exactly the fee."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=5_000)
    balance = lab.settlement.balance_for(merchant_id=lab.merchant_id)
    assert balance.balance == iqd(100_000)
    assert balance_of(
        HUDHUD_REVENUE, lab.uow.ledger.entries_for_account(HUDHUD_REVENUE)
    ).amount == iqd(5_000)


def test_a_merchant_balance_is_derived_from_the_ledger() -> None:
    lab = build_lab()
    collect_cash(lab, goods=60_000, fee=0)
    collect_cash(lab, goods=40_000, fee=0)
    payable = merchant_payable(lab.merchant_id)
    assert lab.settlement.balance_for(merchant_id=lab.merchant_id).balance == balance_of(
        payable, lab.uow.ledger.entries_for_account(payable)
    ).amount


def test_an_open_payout_reduces_what_is_available_but_not_the_balance() -> None:
    """Otherwise a merchant could request the same money twice over."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(60_000),
        destination_reference="IQ00BANK0001",
    )
    balance = lab.settlement.balance_for(merchant_id=lab.merchant_id)
    assert balance.balance == iqd(100_000)
    assert balance.committed == iqd(60_000)
    assert balance.available == iqd(40_000)


# ------------------------------------------------------------------ PAY-06


def test_a_merchant_can_request_a_payout_by_each_of_the_four_methods() -> None:
    """v6.3 p.31, p.34 — bank transfer, Mastercard, exchange partner, in person."""
    for method in PayoutMethod:
        lab = build_lab()
        collect_cash(lab, goods=100_000, fee=0)
        payout = lab.settlement.request_payout(
            merchant_id=lab.merchant_id,
            method=method,
            amount=iqd(10_000),
            destination_reference=(
                None if method is PayoutMethod.IN_PERSON_AT_HUB else "DEST-1"
            ),
        )
        assert payout.status is PayoutStatus.REQUESTED, method


def test_collecting_in_person_needs_no_destination() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    payout = lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.IN_PERSON_AT_HUB,
        amount=iqd(10_000),
    )
    assert payout.destination_reference is None


def test_the_other_three_methods_need_one() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    for method in (
        PayoutMethod.BANK_TRANSFER,
        PayoutMethod.MASTERCARD,
        PayoutMethod.MONEY_EXCHANGE,
    ):
        with pytest.raises(PayoutDestinationRequired):
            lab.settlement.request_payout(
                merchant_id=lab.merchant_id, method=method, amount=iqd(10_000)
            )


def test_a_merchant_cannot_request_more_than_they_are_owed() -> None:
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    with pytest.raises(InsufficientMerchantBalance):
        lab.settlement.request_payout(
            merchant_id=lab.merchant_id,
            method=PayoutMethod.BANK_TRANSFER,
            amount=iqd(50_000),
            destination_reference="IQ00BANK0001",
        )


def test_only_operations_approves_a_payout() -> None:
    """ADR-0012."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = _requested(lab)
    for wrong in (accountant(), cashier(), driver_actor(lab.driver_id)):
        with pytest.raises(OnlyOperationsApprovesAPayout):
            lab.settlement.approve_payout(payout_id=payout.payout_id, actor=wrong)
    approved = lab.settlement.approve_payout(
        payout_id=payout.payout_id, actor=operations()
    )
    assert approved.status is PayoutStatus.APPROVED


def test_a_rejected_payout_says_why() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = _requested(lab)
    rejected = lab.settlement.reject_payout(
        payout_id=payout.payout_id,
        actor=operations(),
        reason="destination account does not match the merchant",
    )
    assert rejected.status is PayoutStatus.REJECTED
    assert rejected.rejection_reason


def test_a_rejected_payout_cannot_then_be_approved() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = _requested(lab)
    lab.settlement.reject_payout(
        payout_id=payout.payout_id, actor=operations(), reason="wrong account"
    )
    with pytest.raises(PayoutTransitionNotAllowed):
        lab.settlement.approve_payout(payout_id=payout.payout_id, actor=operations())


def test_payouts_can_be_put_on_hold_for_a_merchant() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    account = lab.uow.merchant_accounts.find_for_merchant(lab.merchant_id)
    account.payouts_blocked = True
    account.payouts_blocked_reason = "under review"
    lab.uow.merchant_accounts.save(account)
    with pytest.raises(PayoutsBlockedForThisMerchant):
        _requested(lab)


# ------------------------------------------------- PAY-07: the one blocked step


def test_paying_a_payout_out_is_refused() -> None:
    """PAY-07 — the procedure per method is an Open Item needing an accountant."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = _requested(lab)
    lab.settlement.approve_payout(payout_id=payout.payout_id, actor=operations())
    with pytest.raises(PayoutProcedureNotDefined) as caught:
        lab.settlement.pay_payout(payout_id=payout.payout_id, actor=operations())
    assert "Open Item" in str(caught.value)
    assert "accountant" in str(caught.value)


def test_the_refusal_names_the_method_whose_procedure_is_missing() -> None:
    for method in PayoutMethod:
        lab = build_lab()
        collect_cash(lab, goods=100_000, fee=0)
        payout = lab.settlement.request_payout(
            merchant_id=lab.merchant_id,
            method=method,
            amount=iqd(10_000),
            destination_reference=(
                None if method is PayoutMethod.IN_PERSON_AT_HUB else "DEST-2"
            ),
        )
        lab.settlement.approve_payout(payout_id=payout.payout_id, actor=operations())
        with pytest.raises(PayoutProcedureNotDefined) as caught:
            lab.settlement.pay_payout(payout_id=payout.payout_id, actor=operations())
        assert method.value in str(caught.value)


def test_the_block_does_not_move_any_money() -> None:
    """A refusal that had already posted would be worse than one that had not."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = _requested(lab)
    lab.settlement.approve_payout(payout_id=payout.payout_id, actor=operations())
    before = lab.settlement.balance_for(merchant_id=lab.merchant_id).balance
    with pytest.raises(PayoutProcedureNotDefined):
        lab.settlement.pay_payout(payout_id=payout.payout_id, actor=operations())
    assert lab.settlement.balance_for(merchant_id=lab.merchant_id).balance == before


def test_an_unapproved_payout_is_refused_before_pay_07_is_reached() -> None:
    """The state machine still applies: 409 for the wrong state, 501 only for the gap."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = _requested(lab)
    with pytest.raises(PayoutTransitionNotAllowed):
        lab.settlement.pay_payout(payout_id=payout.payout_id, actor=operations())


def test_paying_still_needs_the_right_actor() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    payout = _requested(lab)
    lab.settlement.approve_payout(payout_id=payout.payout_id, actor=operations())
    with pytest.raises(OnlyOperationsApprovesAPayout):
        lab.settlement.pay_payout(payout_id=payout.payout_id, actor=accountant())


# ------------------------------------------- PAY-08: the charge, and the waiver


def test_a_refusal_charges_the_merchant_both_fees() -> None:
    """v6.3 p.38, p.39 — regardless of reason. Implemented, not blocked."""
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    charge = lab.settlement.charge_for_refusal(
        tracking_code="SHP-20260915-000999",
        merchant_id=lab.merchant_id,
        delivery_fee=iqd(5_000),
    )
    assert charge.delivery_fee == iqd(5_000)
    assert charge.return_trip_fee == iqd(TEST_RETURN_TRIP_FEE)
    assert charge.total == iqd(5_000 + TEST_RETURN_TRIP_FEE)


def test_the_refusal_charge_reaches_the_merchants_balance() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=0)
    lab.settlement.charge_for_refusal(
        tracking_code="SHP-20260915-000998",
        merchant_id=lab.merchant_id,
        delivery_fee=iqd(5_000),
    )
    expected = 100_000 - 5_000 - TEST_RETURN_TRIP_FEE
    assert lab.settlement.balance_for(merchant_id=lab.merchant_id).balance == iqd(
        expected
    )


def test_the_charge_takes_no_reason_because_no_reason_changes_it() -> None:
    """v6.3 says "regardless of reason" — accepting one would imply there might be."""
    signature = inspect.signature(build_lab().settlement.charge_for_refusal)
    assert "reason" not in signature.parameters


def test_no_tariff_means_no_charge_rather_than_a_guess() -> None:
    lab = build_lab(return_trip_fee=None)
    with pytest.raises(TariffNotConfigured):
        lab.settlement.charge_for_refusal(
            tracking_code="SHP-20260915-000997",
            merchant_id=lab.merchant_id,
            delivery_fee=iqd(5_000),
        )


def test_waiving_the_return_fee_is_refused() -> None:
    """PAY-08 — whether HUDHUD may waive it is the undecided part."""
    lab = build_lab()
    with pytest.raises(ReturnFeeWaiverNotDecided) as caught:
        lab.settlement.waive_return_trip_fee(
            tracking_code="SHP-20260915-000996",
            merchant_id=lab.merchant_id,
            amount=iqd(TEST_RETURN_TRIP_FEE),
            actor=operations(),
            note="goodwill",
        )
    assert "Open Item" in str(caught.value)


def test_the_waiver_is_off_by_default() -> None:
    assert build_lab().settlement.return_fee_waiver_permitted is False


def test_the_waiver_works_the_moment_the_business_permits_it() -> None:
    """Written in full behind one flag, so deciding is a setting and not a rewrite."""
    lab = build_lab(waiver_permitted=True)
    collect_cash(lab, goods=100_000, fee=0)
    lab.settlement.charge_for_refusal(
        tracking_code="SHP-20260915-000995",
        merchant_id=lab.merchant_id,
        delivery_fee=iqd(5_000),
    )
    charged = lab.settlement.balance_for(merchant_id=lab.merchant_id).balance
    lab.settlement.waive_return_trip_fee(
        tracking_code="SHP-20260915-000995",
        merchant_id=lab.merchant_id,
        amount=iqd(TEST_RETURN_TRIP_FEE),
        actor=operations(),
        note="merchant was not at fault",
    )
    after = lab.settlement.balance_for(merchant_id=lab.merchant_id).balance
    assert after == charged + iqd(TEST_RETURN_TRIP_FEE)


def test_a_permitted_waiver_still_needs_operations_and_a_note() -> None:
    lab = build_lab(waiver_permitted=True)
    with pytest.raises(OnlyOperationsApprovesAPayout):
        lab.settlement.waive_return_trip_fee(
            tracking_code="SHP-20260915-000994",
            merchant_id=lab.merchant_id,
            amount=iqd(1_000),
            actor=accountant(),
            note="because",
        )
    with pytest.raises(ResolutionNoteRequired):
        lab.settlement.waive_return_trip_fee(
            tracking_code="SHP-20260915-000994",
            merchant_id=lab.merchant_id,
            amount=iqd(1_000),
            actor=operations(),
            note="   ",
        )


def test_charging_is_never_blocked_by_the_waiver_being_undecided() -> None:
    """The two must not be coupled: v6.3 settles one and leaves the other open."""
    lab = build_lab(waiver_permitted=False)
    charge = lab.settlement.charge_for_refusal(
        tracking_code="SHP-20260915-000993",
        merchant_id=lab.merchant_id,
        delivery_fee=iqd(5_000),
    )
    assert charge.total.minor_units > 0


# ---------------------------------------------------- DRV-A12, DRV-A13


def test_a_balanced_count_resolves_itself() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(50_000),
    )
    assert reconciliation.outcome is ReconciliationOutcome.BALANCED
    assert reconciliation.status is ReconciliationStatus.RESOLVED
    assert reconciliation.blocks_payout is False


def test_the_expected_figure_comes_from_the_ledger_not_the_caller() -> None:
    """Otherwise the count and the expectation agree by construction."""
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    assert reconciliation.expected == iqd(50_000)
    assert reconciliation.outcome is ReconciliationOutcome.SHORT
    assert reconciliation.difference == iqd(20_000)


def test_a_short_count_blocks_payout_until_it_is_resolved() -> None:
    """v6.3 p.31, p.33 — "investigated before payout"."""
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    with pytest.raises(UnresolvedReconciliationBlocksPayout):
        lab.settlement.assert_no_unresolved_mismatch(
            driver_principal_id=lab.driver_id
        )


def test_an_over_count_is_a_mismatch_too() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(60_000),
    )
    assert reconciliation.outcome is ReconciliationOutcome.OVER
    assert reconciliation.blocks_payout is True


def test_only_operations_resolves_a_mismatch() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    with pytest.raises(OnlyOperationsResolvesAMismatch):
        lab.settlement.resolve_reconciliation(
            reconciliation_id=reconciliation.reconciliation_id,
            actor=driver_actor(lab.driver_id),
            note="I am sure it is fine",
        )


def test_resolving_a_mismatch_brings_the_ledger_into_line() -> None:
    """Otherwise custody keeps showing money that is not there, and every later limit
    check is wrong by the same amount."""
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    result = lab.settlement.resolve_reconciliation(
        reconciliation_id=reconciliation.reconciliation_id,
        actor=operations(),
        note="20,000 short; recovered from the driver next day",
    )
    assert result.adjustment is not None
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(30_000)


def test_resolving_an_over_count_also_squares_the_ledger() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(60_000),
    )
    lab.settlement.resolve_reconciliation(
        reconciliation_id=reconciliation.reconciliation_id,
        actor=operations(),
        note="10,000 over; unidentified, held pending investigation",
    )
    custody = driver_custody(lab.driver_id)
    assert balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount == iqd(60_000)


def test_a_resolution_must_say_what_was_found() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    with pytest.raises(ResolutionNoteRequired):
        lab.settlement.resolve_reconciliation(
            reconciliation_id=reconciliation.reconciliation_id,
            actor=operations(),
            note="  ",
        )


def test_a_mismatch_is_resolved_once() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    lab.settlement.resolve_reconciliation(
        reconciliation_id=reconciliation.reconciliation_id,
        actor=operations(),
        note="found",
    )
    with pytest.raises(ReconciliationAlreadyResolved):
        lab.settlement.resolve_reconciliation(
            reconciliation_id=reconciliation.reconciliation_id,
            actor=operations(),
            note="again",
        )


def test_the_day_is_complete_only_when_both_halves_are_confirmed() -> None:
    """DRV-A12 — parcels handed over *and* cash settled."""
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(30_000),
    )
    assert reconciliation.day_is_complete is False
    partly = lab.settlement.confirm_hub_return(
        reconciliation_id=reconciliation.reconciliation_id, parcels=True, cash=False
    )
    assert partly.day_is_complete is False
    fully = lab.settlement.confirm_hub_return(
        reconciliation_id=reconciliation.reconciliation_id, parcels=False, cash=True
    )
    assert fully.day_is_complete is True


def test_a_settled_driver_has_no_mismatch_blocking_anything() -> None:
    lab = build_lab()
    collect_cash(lab, goods=50_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(50_000),
        reference="HUB-END",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    reconciliation = lab.settlement.open_reconciliation(
        driver_principal_id=lab.driver_id,
        route_day=date(2026, 9, 15),
        counted=iqd(0),
    )
    assert reconciliation.outcome is ReconciliationOutcome.BALANCED
    lab.settlement.assert_no_unresolved_mismatch(driver_principal_id=lab.driver_id)


def _requested(lab):
    return lab.settlement.request_payout(
        merchant_id=lab.merchant_id,
        method=PayoutMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        destination_reference="IQ00BANK0001",
    )


# ------------------------------- a merchant balance needs no administration


def test_a_merchant_has_a_balance_before_anyone_opens_an_account() -> None:
    """The balance lives in the ledger; the account row is only policy.

    A regression test for a real failure found by running the platform: reading a
    balance 404'd unless someone had first called `open_merchant_account`, so every
    merchant who had been paid but never administered — which is all of them — looked
    like they did not exist.
    """
    lab = build_lab()
    fresh_merchant = uuid4()
    lab.cod.record_collection(
        tracking_code=next_tracking_code(),
        merchant_id=fresh_merchant,
        channel=CodPaymentChannel.ONLINE,
        goods_amount=iqd(40_000),
        delivery_fee=iqd(2_000),
    )
    balance = lab.settlement.balance_for(merchant_id=fresh_merchant)
    assert balance.balance == iqd(40_000)
    assert balance.payouts_blocked is False


def test_a_merchant_with_no_account_row_can_request_a_payout() -> None:
    lab = build_lab()
    fresh_merchant = uuid4()
    lab.cod.record_collection(
        tracking_code=next_tracking_code(),
        merchant_id=fresh_merchant,
        channel=CodPaymentChannel.ONLINE,
        goods_amount=iqd(60_000),
        delivery_fee=iqd(0),
    )
    payout = lab.settlement.request_payout(
        merchant_id=fresh_merchant,
        method=PayoutMethod.IN_PERSON_AT_HUB,
        amount=iqd(10_000),
    )
    assert payout.status is PayoutStatus.REQUESTED


def test_blocking_payouts_records_a_row_and_a_reason() -> None:
    """A block is a deliberate act, so unlike a balance it must be written down."""
    lab = build_lab()
    fresh_merchant = uuid4()
    lab.cod.record_collection(
        tracking_code=next_tracking_code(),
        merchant_id=fresh_merchant,
        channel=CodPaymentChannel.ONLINE,
        goods_amount=iqd(60_000),
        delivery_fee=iqd(0),
    )
    lab.settlement.block_payouts(
        merchant_id=fresh_merchant, reason="under investigation"
    )
    balance = lab.settlement.balance_for(merchant_id=fresh_merchant)
    assert balance.payouts_blocked is True
    assert balance.payouts_blocked_reason == "under investigation"
    with pytest.raises(PayoutsBlockedForThisMerchant):
        lab.settlement.request_payout(
            merchant_id=fresh_merchant,
            method=PayoutMethod.IN_PERSON_AT_HUB,
            amount=iqd(1_000),
        )


def test_a_block_can_be_lifted() -> None:
    lab = build_lab()
    lab.settlement.block_payouts(merchant_id=lab.merchant_id, reason="checking")
    lab.settlement.block_payouts(merchant_id=lab.merchant_id, reason="", blocked=False)
    assert (
        lab.settlement.balance_for(merchant_id=lab.merchant_id).payouts_blocked is False
    )
