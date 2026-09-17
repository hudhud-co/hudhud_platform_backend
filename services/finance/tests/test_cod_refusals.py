"""The three things v6.3 says HUDHUD does *not* do, stated out loud.

Each of these could have been implemented as an absence — no route, no enum member, no
code path. They are refusals instead, because an absence looks like an oversight and a
refusal reads as a decision. Somebody adding a wallet payment option in a year's time
should have to delete a test that explains why it is not there.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from finance_fixtures import build_lab, collect_cash, iqd, next_tracking_code

from finance.domain.errors import (
    CollectionNotFound,
    ExchangeTransferCannotConfirmPayment,
    ReceiverWalletPaymentNotOffered,
    RefundOnlyWhenTheReceiverPaidHudhud,
)
from finance.domain.ledger import (
    BANK,
    balance_of,
    merchant_payable,
    receiver_refund_payable,
)
from finance.domain.value_objects import (
    RECEIVER_WALLET_PAYMENT_OFFERED,
    CodPaymentChannel,
)

# ------------------------------------------------------------------ PAY-03


def test_there_is_no_wallet_channel_for_receivers() -> None:
    """v6.3 p.33 — "No digital wallet payment option for receivers"."""
    assert {channel.value for channel in CodPaymentChannel} == {
        "ONLINE",
        "POS_CARD",
        "CASH",
        "AT_HUB_IN_PERSON",
    }


def test_the_absence_is_recorded_as_a_decision() -> None:
    assert RECEIVER_WALLET_PAYMENT_OFFERED is False


def test_offering_a_receiver_wallet_payment_is_refused_out_loud() -> None:
    lab = build_lab()
    with pytest.raises(ReceiverWalletPaymentNotOffered) as caught:
        lab.cod.record_receiver_wallet_payment(amount=iqd(1_000))
    assert "p.33" in str(caught.value)


# ------------------------------------------------------------------ PAY-04


def test_an_exchange_transfer_cannot_confirm_the_original_payment() -> None:
    """v6.3 p.33 — it settles cash already collected, and that is all."""
    lab = build_lab()
    with pytest.raises(ExchangeTransferCannotConfirmPayment) as caught:
        lab.cod.confirm_payment_by_exchange_transfer(reference="EX-1")
    assert "settles collected cash" in str(caught.value)


# ------------------------------------------------------------------ PAY-02


def test_a_receiver_can_pay_at_a_hub_in_person() -> None:
    """PAY-02 — v6.3 p.33 includes this case, and it is paid at once."""
    lab = build_lab()
    code = next_tracking_code()
    result = lab.cod.record_collection(
        tracking_code=code,
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.AT_HUB_IN_PERSON,
        goods_amount=iqd(30_000),
        delivery_fee=iqd(1_500),
    )
    assert result.collection.is_paid is True
    assert result.collection.enters_driver_custody is False


def test_paying_at_a_hub_needs_no_driver() -> None:
    """Nobody carried it, so no custody is involved."""
    lab = build_lab()
    result = lab.cod.record_collection(
        tracking_code=next_tracking_code(),
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.AT_HUB_IN_PERSON,
        goods_amount=iqd(10_000),
        delivery_fee=iqd(0),
    )
    assert result.collection.driver_principal_id is None


# ------------------------------------------------------------------ PAY-09


def test_a_refund_is_recognised_where_the_receiver_paid_hudhud() -> None:
    """v6.3 p.39 — online, card, cash at the door and paying at a hub all qualify."""
    lab = build_lab()
    receiver = uuid4()
    for channel in CodPaymentChannel:
        code = next_tracking_code()
        lab.cod.record_collection(
            tracking_code=code,
            merchant_id=lab.merchant_id,
            channel=channel,
            goods_amount=iqd(20_000),
            delivery_fee=iqd(0),
            driver_principal_id=(
                lab.driver_id if channel is CodPaymentChannel.CASH else None
            ),
        )
        entry = lab.cod.recognise_refund(
            tracking_code=code,
            receiver_principal_id=receiver,
            amount=iqd(5_000),
        )
        assert entry.total == iqd(5_000), channel


def test_a_refund_moves_the_liability_from_the_merchant_to_the_receiver() -> None:
    """It is not new money: what HUDHUD owed the merchant it now owes the receiver."""
    lab = build_lab()
    receiver = uuid4()
    collect_cash(lab, goods=50_000, fee=0)
    before = lab.settlement.balance_for(merchant_id=lab.merchant_id).balance
    code = next_tracking_code()
    lab.cod.record_collection(
        tracking_code=code,
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.ONLINE,
        goods_amount=iqd(20_000),
        delivery_fee=iqd(0),
    )
    lab.cod.recognise_refund(
        tracking_code=code, receiver_principal_id=receiver, amount=iqd(8_000)
    )
    after = lab.settlement.balance_for(merchant_id=lab.merchant_id).balance
    owed_to_receiver = balance_of(
        receiver_refund_payable(receiver),
        lab.uow.ledger.entries_for_account(receiver_refund_payable(receiver)),
    ).amount
    assert after == before + iqd(20_000) - iqd(8_000)
    assert owed_to_receiver == iqd(8_000)


def test_a_refund_for_an_unknown_parcel_is_refused() -> None:
    lab = build_lab()
    with pytest.raises(CollectionNotFound):
        lab.cod.recognise_refund(
            tracking_code="SHP-20260915-404404",
            receiver_principal_id=uuid4(),
            amount=iqd(1_000),
        )


def test_paying_a_refund_takes_it_out_of_the_bank() -> None:
    lab = build_lab()
    receiver = uuid4()
    code = next_tracking_code()
    lab.cod.record_collection(
        tracking_code=code,
        merchant_id=lab.merchant_id,
        channel=CodPaymentChannel.ONLINE,
        goods_amount=iqd(20_000),
        delivery_fee=iqd(0),
    )
    lab.cod.recognise_refund(
        tracking_code=code, receiver_principal_id=receiver, amount=iqd(8_000)
    )
    bank_before = balance_of(BANK, lab.uow.ledger.entries_for_account(BANK)).amount
    lab.cod.pay_refund(receiver_principal_id=receiver, amount=iqd(8_000))
    bank_after = balance_of(BANK, lab.uow.ledger.entries_for_account(BANK)).amount
    assert bank_after == bank_before - iqd(8_000)
    assert balance_of(
        receiver_refund_payable(receiver),
        lab.uow.ledger.entries_for_account(receiver_refund_payable(receiver)),
    ).amount == iqd(0)


def test_the_refund_rule_names_the_channel_when_it_refuses() -> None:
    """So an operator reading the error knows why, not just that."""
    assert RefundOnlyWhenTheReceiverPaidHudhud("SOME_CHANNEL").channel == "SOME_CHANNEL"


def test_a_merchant_payable_is_the_only_place_a_merchant_balance_lives() -> None:
    lab = build_lab()
    collect_cash(lab, goods=40_000, fee=0)
    payable = merchant_payable(lab.merchant_id)
    assert balance_of(
        payable, lab.uow.ledger.entries_for_account(payable)
    ).amount == lab.settlement.balance_for(merchant_id=lab.merchant_id).balance
