"""When a COD parcel counts as paid, and what that does to the books (PAY-01 … PAY-04).

v6.3 p.33 lists exactly three ways a COD parcel counts as paid — an online payment that
succeeded, a POS card approval, or **physical cash collected and handed to the hub
cashier** — plus the case of a receiver coming to a hub to pay in person (PAY-02).

Two things are refused rather than merely absent:

* a receiver cannot pay from a wallet (PAY-03). v6.3 offers no such option, so the
  service says so out loud instead of failing to have a route;
* an exchange-office transfer cannot confirm the original payment (PAY-04). It settles
  cash the driver already collected, which is a different thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from finance.application.publishing import enqueue
from finance.domain.entities import CodCollection
from finance.domain.errors import (
    CollectionAlreadyRecorded,
    CollectionNotFound,
    ExchangeTransferCannotConfirmPayment,
    NothingToCollect,
    ReceiverWalletPaymentNotOffered,
    RefundOnlyWhenTheReceiverPaidHudhud,
    TariffNotConfigured,
)
from finance.domain.ledger import (
    BANK,
    HUB_CASH,
    HUDHUD_REVENUE,
    EntryDraft,
    JournalEntry,
    balance_of,
    driver_custody,
    merchant_payable,
    receiver_refund_payable,
)
from finance.domain.money import Money
from finance.domain.value_objects import (
    CodPaymentChannel,
    JournalReason,
)
from finance.infrastructure.contracts.envelopes import (
    build_cash_limit_breached_envelope,
    build_cod_settled_envelope,
)
from finance.ports.repository import FinanceUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    collection: CodCollection
    entry: JournalEntry


#: Where the money lands the instant it is taken, per channel. Only cash goes to a
#: person; the rest are already HUDHUD's (DRV-L15, v6.3 p.31).
_LANDING = {
    CodPaymentChannel.ONLINE: JournalReason.COD_ONLINE_COLLECTED,
    CodPaymentChannel.POS_CARD: JournalReason.COD_CARD_COLLECTED,
    CodPaymentChannel.CASH: JournalReason.COD_CASH_COLLECTED,
    CodPaymentChannel.AT_HUB_IN_PERSON: JournalReason.COD_PAID_AT_HUB,
}


class CodService:
    def __init__(
        self, unit_of_work: FinanceUnitOfWork, *, outbox_max_attempts: int = 5
    ) -> None:
        self._uow = unit_of_work
        self._outbox_max_attempts = outbox_max_attempts

    def record_collection(
        self,
        *,
        tracking_code: str,
        merchant_id: UUID,
        channel: CodPaymentChannel,
        goods_amount: Money,
        delivery_fee: Money,
        driver_principal_id: UUID | None = None,
        collected_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> CollectionResult:
        """Record what was taken for one parcel, and post it.

        The split matters: the goods are the merchant's money and the delivery fee is
        HUDHUD's, so one collection is one entry with two credits. Recording it as a
        single credit to the merchant would overstate what they are owed by exactly the
        fee, every time.
        """
        self._uow.begin()
        try:
            result = self.record_collection_within_transaction(
                tracking_code=tracking_code,
                merchant_id=merchant_id,
                channel=channel,
                goods_amount=goods_amount,
                delivery_fee=delivery_fee,
                driver_principal_id=driver_principal_id,
                collected_at=collected_at,
                idempotency_key=idempotency_key,
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return result

    def record_collection_within_transaction(
        self,
        *,
        tracking_code: str,
        merchant_id: UUID,
        channel: CodPaymentChannel,
        goods_amount: Money,
        delivery_fee: Money,
        driver_principal_id: UUID | None = None,
        collected_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> CollectionResult:
        """The same work, joining a transaction the caller already opened.

        This is what a durable-inbox handler calls: the inbox holds the transaction so
        that the deduplication record and this posting commit together.
        """
        if goods_amount.is_zero and delivery_fee.is_zero:
            raise NothingToCollect()
        moment = collected_at or _now()

        if idempotency_key:
            existing = self._uow.ledger.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                # A retry of the same collection, not a second one.
                found = self._uow.collections.find_by_tracking_code(tracking_code)
                if found is None:
                    raise CollectionNotFound(tracking_code)
                return CollectionResult(collection=found, entry=existing)
        if self._uow.collections.find_by_tracking_code(tracking_code) is not None:
            raise CollectionAlreadyRecorded(tracking_code)

        total = goods_amount + delivery_fee
        collection = CodCollection(
            collection_id=uuid4(),
            tracking_code=tracking_code,
            merchant_id=merchant_id,
            channel=channel,
            goods_amount=goods_amount,
            delivery_fee=delivery_fee,
            collected_at=moment,
            driver_principal_id=driver_principal_id,
            # PAY-01 — cash is not paid until it reaches a hub cashier; everything
            # else is HUDHUD's already, so it settles the moment it is taken.
            settled_at=None if channel is CodPaymentChannel.CASH else moment,
        )
        destination = (
            driver_custody(driver_principal_id)
            if channel is CodPaymentChannel.CASH
            else (HUB_CASH if channel is CodPaymentChannel.AT_HUB_IN_PERSON else BANK)
        )
        draft = EntryDraft(
            reason=_LANDING[channel],
            occurred_at=moment,
            recorded_by_actor_id=driver_principal_id,
            subject_kind="collection",
            subject_id=collection.collection_id,
            idempotency_key=idempotency_key,
            memo=f"COD {tracking_code}",
        ).debit(destination, total)
        if not goods_amount.is_zero:
            draft.credit(merchant_payable(merchant_id), goods_amount)
        if not delivery_fee.is_zero:
            draft.credit(HUDHUD_REVENUE, delivery_fee)

        entry = draft.build(uuid4())
        self._uow.ledger.append(entry)
        collection.journal_entry_id = entry.entry_id
        self._uow.collections.save(collection)

        if collection.is_paid:
            # Card, online and pay-at-hub are settled the moment they are taken, so
            # the fact goes out here rather than waiting for a deposit that will
            # never come. Cash publishes when it reaches a hub cashier instead.
            settled_payload, settled_subject = build_cod_settled_envelope(
                collection=collection,
                aggregate_version=collection.version,
                event_id=uuid4(),
                correlation_id=uuid4(),
            )
            enqueue(
                self._uow,
                settled_payload,
                settled_subject,
                aggregate_id=collection.collection_id,
                aggregate_version=collection.version,
                max_attempts=self._outbox_max_attempts,
            )

        if channel is CodPaymentChannel.CASH and driver_principal_id is not None:
            self._announce_a_breach(driver_principal_id, moment)
        return CollectionResult(collection=collection, entry=entry)

    def _announce_a_breach(self, driver_principal_id: UUID, moment: datetime) -> None:
        """DRV-A06 — say so when this collection took the driver to their limit.

        Announced, not enforced: the parcel was already at a door and ADR-0003 makes
        that irreversible. What the fact does is stop the *next* one being assigned.
        """
        account = self._uow.driver_accounts.find_for_driver(driver_principal_id)
        if account is None:
            return
        custody = driver_custody(driver_principal_id)
        held = balance_of(
            custody, self._uow.ledger.entries_for_account(custody)
        ).amount
        if held < account.limit:
            return
        # The account's own version orders these facts; two breaches in one day are two
        # facts, so the version advances with each.
        account.version += 1
        self._uow.driver_accounts.save(account)
        payload, subject = build_cash_limit_breached_envelope(
            driver_principal_id=driver_principal_id,
            held=held,
            limit=account.limit,
            utilisation_percent=account.utilisation_percent(held),
            observed_at=moment,
            aggregate_version=account.version,
            event_id=uuid4(),
            correlation_id=uuid4(),
        )
        enqueue(
            self._uow,
            payload,
            subject,
            aggregate_id=driver_principal_id,
            aggregate_version=account.version,
            max_attempts=self._outbox_max_attempts,
        )

    def record_pickup_fee(
        self,
        *,
        tracking_code: str,
        merchant_id: UUID,
        amount: Money,
        driver_principal_id: UUID,
        by_card: bool = False,
        collected_at: datetime | None = None,
    ) -> JournalEntry:
        """DRV-A11, PAY-10 — the courier fee taken from the sender at pickup.

        Entirely HUDHUD's revenue, and the sender is not the merchant's customer here,
        so nothing is credited to the merchant. Cash goes into the driver's custody like
        any other cash; a card goes straight to the bank.
        """
        if amount.is_zero:
            raise NothingToCollect()
        moment = collected_at or _now()
        self._uow.begin()
        try:
            entry = EntryDraft(
                reason=JournalReason.PICKUP_FEE_COLLECTED,
                occurred_at=moment,
                recorded_by_actor_id=driver_principal_id,
                subject_kind="pickup_fee",
                subject_id=None,
                memo=f"courier fee {tracking_code}",
            ).debit(
                BANK if by_card else driver_custody(driver_principal_id), amount
            ).credit(HUDHUD_REVENUE, amount).build(uuid4())
            self._uow.ledger.append(entry)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        _ = merchant_id
        return entry

    # ------------------------------------------------------------ what is refused

    def record_receiver_wallet_payment(self, **_: object) -> None:
        """PAY-03 — there is no such payment method.

        This exists so the absence is explicit and testable. v6.3 p.33 states plainly
        that receivers have no digital wallet payment option; a service that simply had
        no route here would look like an oversight rather than a decision.
        """
        raise ReceiverWalletPaymentNotOffered()

    def confirm_payment_by_exchange_transfer(self, **_: object) -> None:
        """PAY-04 — an exchange transfer settles collected cash; it confirms nothing.

        Same reasoning: refusing loudly is better than being silently unable.
        """
        raise ExchangeTransferCannotConfirmPayment()

    # ------------------------------------------------------------------ PAY-09

    def recognise_refund(
        self,
        *,
        tracking_code: str,
        receiver_principal_id: UUID,
        amount: Money,
        actor_id: UUID | None = None,
    ) -> JournalEntry:
        """v6.3 p.39 — HUDHUD refunds only a receiver who paid HUDHUD directly.

        Cash handed to a driver at the door was HUDHUD's to hold; an online or card
        payment was HUDHUD's outright. In every other case the money never reached
        HUDHUD, so a refund is between the merchant and the receiver and this refuses.
        """
        self._uow.begin()
        try:
            collection = self._uow.collections.find_by_tracking_code(tracking_code)
            if collection is None:
                raise CollectionNotFound(tracking_code)
            if collection.channel not in _PAID_HUDHUD_DIRECTLY:
                raise RefundOnlyWhenTheReceiverPaidHudhud(collection.channel.value)
            moment = _now()
            entry = (
                EntryDraft(
                    reason=JournalReason.RECEIVER_REFUND_RECOGNISED,
                    occurred_at=moment,
                    recorded_by_actor_id=actor_id,
                    subject_kind="collection",
                    subject_id=collection.collection_id,
                    memo=f"refund recognised for {tracking_code}",
                )
                # The merchant's payable falls by what is being given back, and a new
                # liability to the receiver takes its place.
                .debit(merchant_payable(collection.merchant_id), amount)
                .credit(receiver_refund_payable(receiver_principal_id), amount)
                .build(uuid4())
            )
            self._uow.ledger.append(entry)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return entry

    def pay_refund(
        self,
        *,
        receiver_principal_id: UUID,
        amount: Money,
        actor_id: UUID | None = None,
    ) -> JournalEntry:
        self._uow.begin()
        try:
            entry = (
                EntryDraft(
                    reason=JournalReason.RECEIVER_REFUND_PAID,
                    occurred_at=_now(),
                    recorded_by_actor_id=actor_id,
                    subject_kind="refund",
                    subject_id=receiver_principal_id,
                    memo="refund paid",
                )
                .debit(receiver_refund_payable(receiver_principal_id), amount)
                .credit(BANK, amount)
                .build(uuid4())
            )
            self._uow.ledger.append(entry)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return entry

    # ------------------------------------------------------------------ reads

    def collection_for(self, *, tracking_code: str) -> CodCollection:
        self._uow.begin()
        try:
            found = self._uow.collections.find_by_tracking_code(tracking_code)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if found is None:
            raise CollectionNotFound(tracking_code)
        return found

    def unsettled_for_driver(
        self, *, driver_principal_id: UUID
    ) -> tuple[CodCollection, ...]:
        self._uow.begin()
        try:
            found = self._uow.collections.list_unsettled_for_driver(driver_principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found


#: The channels where the receiver's money actually reached HUDHUD (PAY-09).
_PAID_HUDHUD_DIRECTLY = frozenset(
    {
        CodPaymentChannel.ONLINE,
        CodPaymentChannel.POS_CARD,
        CodPaymentChannel.AT_HUB_IN_PERSON,
        CodPaymentChannel.CASH,
    }
)


def assert_tariff_configured(amount: Money | None, what: str) -> Money:
    """No fee schedule means no charge. A guessed amount is worse than a refusal."""
    if amount is None:
        raise TariffNotConfigured(what)
    return amount
