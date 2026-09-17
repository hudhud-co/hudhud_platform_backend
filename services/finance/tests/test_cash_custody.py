"""Driver cash custody: the limit, the three deposit routes, and who may confirm each.

The subtle rule these tests exist to pin down is the one v6.3 states twice and in two
different places: an exchange-office transfer **frees the driver's limit immediately**
(p.31) but **does not confirm the original payment** (p.33). Both are true, and they are
only both true because the money sits in `EXCHANGE_IN_TRANSIT` in between.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from finance_fixtures import (
    TEST_CASH_LIMIT,
    accountant,
    build_lab,
    cashier,
    collect_cash,
    driver_actor,
    iqd,
    operations,
    receipt,
)

from finance.domain.errors import (
    CashLimitExceeded,
    CashLimitMustBePositive,
    CodAssignmentBlocked,
    DepositAlreadyDecided,
    DepositEvidenceRequired,
    DriverAccountNotFound,
    HubRequiredForACashierDeposit,
    InvalidDepositReference,
    MoreThanTheDriverHolds,
    OnlyACashierConfirmsAHubDeposit,
    OnlyAnAccountantVerifiesAnExchangeReceipt,
    RejectionReasonRequired,
)
from finance.domain.ledger import (
    BANK,
    EXCHANGE_IN_TRANSIT,
    HUB_CASH,
    balance_of,
    driver_custody,
)
from finance.domain.value_objects import (
    CodPaymentChannel,
    DepositMethod,
    DepositStatus,
)

# ------------------------------------------------------------------ the account


def test_a_driver_has_one_cash_account() -> None:
    lab = build_lab()
    again = lab.cash.open_account(driver_principal_id=lab.driver_id)
    assert again.driver_principal_id == lab.driver_id


def test_the_default_limit_comes_from_configuration() -> None:
    """DRV-A06 — v6.3 says a limit exists; it never says what it is."""
    lab = build_lab(cash_limit=750_000)
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).limit == iqd(750_000)


def test_a_limit_of_zero_is_refused() -> None:
    """It would stop the driver working, which is not what a limit is for."""
    lab = build_lab()
    with pytest.raises(CashLimitMustBePositive):
        lab.cash.set_limit(driver_principal_id=lab.driver_id, limit=iqd(0))


def test_an_unknown_driver_has_no_account() -> None:
    lab = build_lab()
    with pytest.raises(DriverAccountNotFound):
        lab.cash.position_for(driver_principal_id=uuid4())


# ------------------------------------------------------------------ the position


def test_collected_cash_shows_as_held() -> None:
    lab = build_lab()
    collect_cash(lab, goods=100_000, fee=5_000)
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).held == iqd(105_000)


def test_held_cash_is_derived_from_the_ledger_not_stored() -> None:
    """A stored total is a second source of truth for the same fact, and they drift."""
    lab = build_lab()
    collect_cash(lab, goods=60_000, fee=0)
    custody = driver_custody(lab.driver_id)
    from_ledger = balance_of(
        custody, lab.uow.ledger.entries_for_account(custody)
    ).amount
    assert from_ledger == lab.cash.position_for(driver_principal_id=lab.driver_id).held


def test_utilisation_is_an_integer_percentage() -> None:
    lab = build_lab(cash_limit=200_000)
    collect_cash(lab, goods=50_000, fee=0)
    position = lab.cash.position_for(driver_principal_id=lab.driver_id)
    assert position.utilisation_percent == 25
    assert isinstance(position.utilisation_percent, int)


def test_headroom_never_goes_negative() -> None:
    lab = build_lab(cash_limit=100_000)
    collect_cash(lab, goods=150_000, fee=0)
    position = lab.cash.position_for(driver_principal_id=lab.driver_id)
    assert position.headroom == iqd(0)
    assert position.over_limit is True


# ------------------------------------------------------------------ the limit


def test_a_driver_under_their_limit_may_take_more_cod() -> None:
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    lab.cash.assert_may_take_more_cod(driver_principal_id=lab.driver_id)


def test_a_driver_at_their_limit_may_not_be_assigned_more_cod() -> None:
    lab = build_lab(cash_limit=100_000)
    collect_cash(lab, goods=100_000, fee=0)
    with pytest.raises(CashLimitExceeded):
        lab.cash.assert_may_take_more_cod(driver_principal_id=lab.driver_id)


def test_the_limit_blocks_assignment_and_not_a_delivery_in_progress() -> None:
    """ADR-0003 — physical delivery is irreversible; a parcel at a door is handed over.

    So a driver already over their limit can still have their next collection recorded:
    what the limit refuses is giving them another parcel, and that is a different call.
    """
    lab = build_lab(cash_limit=50_000)
    collect_cash(lab, goods=50_000, fee=0)
    with pytest.raises(CashLimitExceeded):
        lab.cash.assert_may_take_more_cod(driver_principal_id=lab.driver_id)
    # The collection still records, because the parcel was already at the door.
    collect_cash(lab, goods=20_000, fee=0)
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).held == iqd(70_000)


def test_operations_can_block_cod_independently_of_the_limit() -> None:
    lab = build_lab()
    lab.cash.block_cod(driver_principal_id=lab.driver_id, reason="under investigation")
    with pytest.raises(CodAssignmentBlocked):
        lab.cash.assert_may_take_more_cod(driver_principal_id=lab.driver_id)


def test_a_block_can_be_lifted() -> None:
    lab = build_lab()
    lab.cash.block_cod(driver_principal_id=lab.driver_id, reason="checking")
    lab.cash.block_cod(driver_principal_id=lab.driver_id, reason="", blocked=False)
    lab.cash.assert_may_take_more_cod(driver_principal_id=lab.driver_id)


# ------------------------------------------------------------------ deposits


def test_a_deposit_needs_an_amount_a_reference_and_a_receipt() -> None:
    """DRV-A07 — Driver App v8 disables submission without all three."""
    lab = build_lab()
    collect_cash(lab)
    with pytest.raises(DepositEvidenceRequired):
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=DepositMethod.BANK_TRANSFER,
            amount=iqd(0),
            reference="TRX-1",
            receipt=receipt(),
        )
    with pytest.raises(DepositEvidenceRequired):
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=DepositMethod.BANK_TRANSFER,
            amount=iqd(1_000),
            reference="   ",
            receipt=receipt(),
        )


def test_a_reference_must_look_like_one() -> None:
    lab = build_lab()
    collect_cash(lab)
    with pytest.raises(InvalidDepositReference):
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=DepositMethod.BANK_TRANSFER,
            amount=iqd(1_000),
            reference="!!",
            receipt=receipt(),
        )


def test_a_hub_deposit_names_the_hub_that_took_the_cash() -> None:
    lab = build_lab()
    collect_cash(lab)
    with pytest.raises(HubRequiredForACashierDeposit):
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=DepositMethod.HUB_CASHIER,
            amount=iqd(1_000),
            reference="HUB-001",
            receipt=receipt(),
        )


def test_a_driver_cannot_deposit_cash_they_do_not_hold() -> None:
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    with pytest.raises(MoreThanTheDriverHolds):
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=DepositMethod.BANK_TRANSFER,
            amount=iqd(20_000),
            reference="TRX-2",
            receipt=receipt(),
        )


def test_every_deposit_method_frees_the_limit_at_once() -> None:
    """DRV-A08, DRV-A09 — v6.3 p.31: the driver keeps collecting straight away."""
    for method, hub in (
        (DepositMethod.HUB_CASHIER, uuid4()),
        (DepositMethod.EXCHANGE_OFFICE, None),
        (DepositMethod.BANK_TRANSFER, None),
    ):
        lab = build_lab(cash_limit=100_000)
        collect_cash(lab, goods=100_000, fee=0)
        with pytest.raises(CashLimitExceeded):
            lab.cash.assert_may_take_more_cod(driver_principal_id=lab.driver_id)
        result = lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=method,
            amount=iqd(100_000),
            reference=f"REF-{method.value}",
            receipt=receipt(),
            hub_id=hub,
        )
        assert result.held_after == iqd(0), method
        # Freed before anyone has verified anything — which is the point.
        assert result.deposit.status is DepositStatus.PENDING_VERIFICATION
        lab.cash.assert_may_take_more_cod(driver_principal_id=lab.driver_id)


def test_each_method_puts_the_money_somewhere_different() -> None:
    hub = uuid4()
    for method, account, hub_id in (
        (DepositMethod.HUB_CASHIER, HUB_CASH, hub),
        (DepositMethod.EXCHANGE_OFFICE, EXCHANGE_IN_TRANSIT, None),
        (DepositMethod.BANK_TRANSFER, BANK, None),
    ):
        lab = build_lab()
        collect_cash(lab, goods=40_000, fee=0)
        lab.cash.submit_deposit(
            driver_principal_id=lab.driver_id,
            method=method,
            amount=iqd(40_000),
            reference=f"R-{method.value}",
            receipt=receipt(),
            hub_id=hub_id,
        )
        landed = balance_of(
            account, lab.uow.ledger.entries_for_account(account)
        ).amount
        assert landed == iqd(40_000), method


# ------------------------------------------------------------------ who confirms


def test_only_an_accountant_verifies_an_exchange_receipt() -> None:
    """v6.3 p.31 names an accountant specifically."""
    lab = build_lab()
    collect_cash(lab, goods=30_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(30_000),
        reference="EX-77",
        receipt=receipt(),
    ).deposit
    with pytest.raises(OnlyAnAccountantVerifiesAnExchangeReceipt):
        lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    with pytest.raises(OnlyAnAccountantVerifiesAnExchangeReceipt):
        lab.cash.confirm_deposit(
            deposit_id=deposit.deposit_id, actor=driver_actor(lab.driver_id)
        )
    confirmed = lab.cash.confirm_deposit(
        deposit_id=deposit.deposit_id, actor=accountant()
    )
    assert confirmed.status is DepositStatus.CONFIRMED


def test_the_driver_who_deposited_cannot_confirm_it() -> None:
    """The one who hands the money over is never the one who says it arrived."""
    lab = build_lab()
    collect_cash(lab, goods=20_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(20_000),
        reference="HUB-9",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    with pytest.raises(OnlyACashierConfirmsAHubDeposit):
        lab.cash.confirm_deposit(
            deposit_id=deposit.deposit_id, actor=driver_actor(lab.driver_id)
        )


def test_a_verified_exchange_receipt_moves_the_money_to_the_bank() -> None:
    lab = build_lab()
    collect_cash(lab, goods=30_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(30_000),
        reference="EX-78",
        receipt=receipt(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=accountant())
    assert balance_of(
        EXCHANGE_IN_TRANSIT, lab.uow.ledger.entries_for_account(EXCHANGE_IN_TRANSIT)
    ).amount == iqd(0)
    assert balance_of(BANK, lab.uow.ledger.entries_for_account(BANK)).amount == iqd(
        30_000
    )


def test_a_deposit_is_decided_once() -> None:
    lab = build_lab()
    collect_cash(lab, goods=15_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(15_000),
        reference="EX-79",
        receipt=receipt(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=accountant())
    with pytest.raises(DepositAlreadyDecided):
        lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=accountant())


def test_a_rejected_deposit_puts_the_cash_back_in_the_drivers_custody() -> None:
    """Until somebody finds where it went, the driver is who last physically had it."""
    lab = build_lab()
    collect_cash(lab, goods=25_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(25_000),
        reference="EX-80",
        receipt=receipt(),
    ).deposit
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).held == iqd(0)
    lab.cash.reject_deposit(
        deposit_id=deposit.deposit_id,
        actor=accountant(),
        reason="receipt does not match the amount",
    )
    assert lab.cash.position_for(driver_principal_id=lab.driver_id).held == iqd(25_000)


def test_a_rejection_must_say_why() -> None:
    lab = build_lab()
    collect_cash(lab, goods=5_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(5_000),
        reference="EX-81",
        receipt=receipt(),
    ).deposit
    with pytest.raises(RejectionReasonRequired):
        lab.cash.reject_deposit(
            deposit_id=deposit.deposit_id, actor=accountant(), reason="  "
        )


# ------------------------------------------------------------------ PAY-01/04


def test_only_a_hub_cashier_deposit_settles_the_parcels_it_covers() -> None:
    """PAY-04 — v6.3 p.33: an exchange transfer settles cash, it confirms no payment."""
    lab = build_lab()
    code = collect_cash(lab, goods=40_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.EXCHANGE_OFFICE,
        amount=iqd(40_000),
        reference="EX-82",
        receipt=receipt(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=accountant())
    assert lab.cod.collection_for(tracking_code=code).is_paid is False


def test_cash_reaching_a_hub_cashier_makes_the_parcel_paid() -> None:
    """PAY-01 — the third of the three ways, and the only one with a hand-over in it."""
    lab = build_lab()
    code = collect_cash(lab, goods=40_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(40_000),
        reference="HUB-82",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    assert lab.cod.collection_for(tracking_code=code).is_paid is True


def test_a_partial_deposit_settles_only_what_it_covers() -> None:
    """Marking everything paid because some money arrived would overstate what is settled."""
    lab = build_lab()
    first = collect_cash(lab, goods=30_000, fee=0)
    second = collect_cash(lab, goods=30_000, fee=0)
    deposit = lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.HUB_CASHIER,
        amount=iqd(30_000),
        reference="HUB-83",
        receipt=receipt(),
        hub_id=uuid4(),
    ).deposit
    lab.cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=cashier())
    assert lab.cod.collection_for(tracking_code=first).is_paid is True
    assert lab.cod.collection_for(tracking_code=second).is_paid is False


# ------------------------------------------------------------------ the reads


def test_settlement_history_carries_every_receipt() -> None:
    """DRV-A10."""
    lab = build_lab()
    collect_cash(lab, goods=10_000, fee=0)
    lab.cash.submit_deposit(
        driver_principal_id=lab.driver_id,
        method=DepositMethod.BANK_TRANSFER,
        amount=iqd(10_000),
        reference="TRX-500",
        receipt=receipt("slip.jpg"),
    )
    history = lab.cash.settlement_history(driver_principal_id=lab.driver_id)
    assert len(history) == 1
    assert history[0].reference == "TRX-500"
    assert history[0].receipt.key == "slip.jpg"


def test_cash_exposure_lists_the_worst_first() -> None:
    """OPS-04 — the view exists to find whoever is carrying too much."""
    lab = build_lab(cash_limit=100_000)
    other = uuid4()
    lab.cash.open_account(driver_principal_id=other)
    collect_cash(lab, goods=20_000, fee=0)
    lab.cod.record_collection(
        tracking_code="SHP-20260915-900001",
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.CASH,
        goods_amount=iqd(90_000),
        delivery_fee=iqd(0),
        driver_principal_id=other,
    )
    exposure = lab.cash.cash_exposure()
    assert exposure[0].driver_principal_id == other
    assert exposure[0].over_limit is False
    assert exposure[0].held == iqd(90_000)
    assert exposure[1].held == iqd(20_000)


def test_the_default_limit_is_what_the_fixture_configured() -> None:
    lab = build_lab()
    assert lab.cash.position_for(
        driver_principal_id=lab.driver_id
    ).limit == iqd(TEST_CASH_LIMIT)


def test_operations_reads_exposure_and_a_driver_does_not() -> None:
    assert operations().may_see_platform_cash_exposure is True
    assert driver_actor(uuid4()).may_see_platform_cash_exposure is False
