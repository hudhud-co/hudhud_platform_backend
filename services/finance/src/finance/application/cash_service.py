"""Driver cash custody: what a driver holds, what they may hold, and getting it out.

The v6.3 rules this implements are on p.31 and p.33, and they interlock in a way that is
easy to get subtly wrong:

* cash collected at the door is in the **driver's** custody, not HUDHUD's (DRV-A05);
* a per-driver limit caps how much they may take on (DRV-A06);
* all three deposit methods move the cash out of custody immediately, which is what frees
  the limit so the driver can keep collecting (DRV-A08, DRV-A09);
* but only cash reaching a **hub cashier** makes the underlying parcels *paid* — an
  exchange transfer explicitly does not confirm the original payment (PAY-04).

So "the driver no longer holds it" and "the parcel is paid" are two different facts, and
`EXCHANGE_IN_TRANSIT` is the account that lets both be true at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from finance.application.publishing import enqueue
from finance.domain.entities import (
    CashExposure,
    CodCollection,
    Deposit,
    DriverCashAccount,
    SettlementHistoryEntry,
)
from finance.domain.errors import (
    CashLimitExceeded,
    CashLimitMustBePositive,
    CodAssignmentBlocked,
    DepositAlreadyDecided,
    DepositEvidenceRequired,
    DepositNotFound,
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
    EntryDraft,
    JournalEntry,
    balance_of,
    driver_custody,
)
from finance.domain.money import Money
from finance.domain.value_objects import (
    DepositMethod,
    DepositStatus,
    EvidenceMediaRef,
    JournalReason,
    is_valid_reference,
)
from finance.infrastructure.contracts.envelopes import (
    build_cod_settled_envelope,
)
from finance.ports.repository import FinanceUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class DepositResult:
    deposit: Deposit
    entry: JournalEntry
    #: What the driver holds after the cash left their hands.
    held_after: Money


@dataclass(frozen=True, slots=True)
class CashPosition:
    """DRV-A05, DRV-A06 — the driver's own view of their money."""

    driver_principal_id: UUID
    held: Money
    limit: Money
    headroom: Money
    utilisation_percent: int
    over_limit: bool
    may_take_more_cod: bool
    open_deposits: int


class CashService:
    def __init__(
        self,
        unit_of_work: FinanceUnitOfWork,
        *,
        default_cash_limit: Money,
        outbox_max_attempts: int = 5,
    ) -> None:
        self._uow = unit_of_work
        # DRV-A06 — a platform default from configuration. v6.3 states that a limit
        # exists, never what it is, so this number is never written in code.
        self._default_limit = default_cash_limit
        self._outbox_max_attempts = outbox_max_attempts

    # ------------------------------------------------------------- the account

    def open_account(
        self, *, driver_principal_id: UUID, limit: Money | None = None
    ) -> DriverCashAccount:
        """Idempotent: a driver has one cash account, whoever asks for it."""
        resolved = limit or self._default_limit
        if resolved.is_zero:
            raise CashLimitMustBePositive()
        self._uow.begin()
        try:
            existing = self._uow.driver_accounts.find_for_driver(driver_principal_id)
            if existing is not None:
                self._uow.commit()
                return existing
            account = DriverCashAccount(
                account_id=uuid4(),
                driver_principal_id=driver_principal_id,
                limit=resolved,
                created_at=_now(),
                updated_at=_now(),
            )
            self._uow.driver_accounts.save(account)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return account

    def set_limit(
        self, *, driver_principal_id: UUID, limit: Money
    ) -> DriverCashAccount:
        if limit.is_zero:
            raise CashLimitMustBePositive()
        self._uow.begin()
        try:
            account = self._account(driver_principal_id)
            account.limit = limit
            account.updated_at = _now()
            account.version += 1
            self._uow.driver_accounts.save(account)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return account

    def block_cod(
        self, *, driver_principal_id: UUID, reason: str, blocked: bool = True
    ) -> DriverCashAccount:
        """Operations stopping a driver taking COD, independently of the limit."""
        self._uow.begin()
        try:
            account = self._account(driver_principal_id)
            account.cod_blocked = blocked
            account.cod_blocked_reason = reason if blocked else None
            account.updated_at = _now()
            account.version += 1
            self._uow.driver_accounts.save(account)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return account

    # ------------------------------------------------------------- the position

    def position_for(self, *, driver_principal_id: UUID) -> CashPosition:
        self._uow.begin()
        try:
            account = self._account(driver_principal_id)
            held = self._held(driver_principal_id)
            open_deposits = sum(
                1
                for deposit in self._uow.deposits.list_for_driver(driver_principal_id)
                if deposit.is_open
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return CashPosition(
            driver_principal_id=driver_principal_id,
            held=held,
            limit=account.limit,
            headroom=account.headroom(held),
            utilisation_percent=account.utilisation_percent(held),
            over_limit=account.is_over_limit(held),
            may_take_more_cod=account.may_take_more_cod(held),
            open_deposits=open_deposits,
        )

    def assert_may_take_more_cod(self, *, driver_principal_id: UUID) -> None:
        """DRV-A06 — the check that blocks **assignment**, not delivery.

        ADR-0003 makes physical delivery irreversible, so a parcel already at a door is
        handed over and its cash collected whatever this says. What this refuses is
        giving the driver another COD parcel to carry.
        """
        self._uow.begin()
        try:
            account = self._account(driver_principal_id)
            held = self._held(driver_principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if account.cod_blocked:
            raise CodAssignmentBlocked(account.cod_blocked_reason or "blocked")
        if not account.may_take_more_cod(held):
            raise CashLimitExceeded(held.minor_units, account.limit.minor_units)

    # ------------------------------------------------------------- OPS-04

    def cash_exposure(self) -> tuple[CashExposure, ...]:
        """OPS-04 — which drivers hold how much, and who is over limit."""
        self._uow.begin()
        try:
            rows = []
            for account in self._uow.driver_accounts.list_all():
                held = self._held(account.driver_principal_id)
                rows.append(
                    CashExposure(
                        driver_principal_id=account.driver_principal_id,
                        held=held,
                        limit=account.limit,
                        utilisation_percent=account.utilisation_percent(held),
                        over_limit=account.is_over_limit(held),
                        open_deposits=sum(
                            1
                            for d in self._uow.deposits.list_for_driver(
                                account.driver_principal_id
                            )
                            if d.is_open
                        ),
                    )
                )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        # Worst first: the view exists to find the driver carrying too much.
        return tuple(
            sorted(rows, key=lambda row: row.held.minor_units, reverse=True)
        )

    # ------------------------------------------------------------- deposits

    def submit_deposit(
        self,
        *,
        driver_principal_id: UUID,
        method: DepositMethod,
        amount: Money,
        reference: str,
        receipt: EvidenceMediaRef,
        hub_id: UUID | None = None,
    ) -> DepositResult:
        """DRV-A07 — amount, reference and receipt, all three, for every method.

        The money leaves the driver's custody here, before anyone has verified anything.
        That is deliberate and is v6.3 p.31: the driver keeps collecting straight away.
        Where it goes depends on the method, and only the hub cashier's route makes the
        parcels it covers *paid*.
        """
        if amount.is_zero:
            raise DepositEvidenceRequired("amount")
        if not reference.strip():
            raise DepositEvidenceRequired("reference")
        if not is_valid_reference(reference.strip()):
            raise InvalidDepositReference()
        if receipt is None:
            raise DepositEvidenceRequired("receipt")
        if method is DepositMethod.HUB_CASHIER and hub_id is None:
            raise HubRequiredForACashierDeposit()

        self._uow.begin()
        try:
            self._account(driver_principal_id)
            held = self._held(driver_principal_id)
            if amount > held:
                raise MoreThanTheDriverHolds(amount.minor_units, held.minor_units)

            moment = _now()
            deposit = Deposit(
                deposit_id=uuid4(),
                driver_principal_id=driver_principal_id,
                method=method,
                amount=amount,
                reference=reference.strip(),
                receipt=receipt,
                status=DepositStatus.PENDING_VERIFICATION,
                submitted_at=moment,
                hub_id=hub_id,
            )
            entry = self._post(
                EntryDraft(
                    reason=_SUBMIT_REASON[method],
                    occurred_at=moment,
                    recorded_by_actor_id=driver_principal_id,
                    subject_kind="deposit",
                    subject_id=deposit.deposit_id,
                    memo=f"{method.value} deposit {deposit.reference}",
                )
                .debit(_SUBMIT_DESTINATION[method], amount)
                .credit(driver_custody(driver_principal_id), amount)
            )
            deposit.submitted_entry_id = entry.entry_id
            # A hub-cashier hand-over and a direct bank transfer land where they land;
            # only the exchange route waits on someone's verification to move again.
            self._uow.deposits.save(deposit)
            held_after = self._held(driver_principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return DepositResult(deposit=deposit, entry=entry, held_after=held_after)

    def confirm_deposit(
        self, *, deposit_id: UUID, actor, moment: datetime | None = None
    ) -> Deposit:
        """The second pair of eyes. Who may give it depends on the method.

        v6.3 p.31 names an accountant for the exchange receipt specifically, and the
        person who took the cash at a hub is the cashier. In neither case is it the
        driver who handed it over.
        """
        when = moment or _now()
        self._uow.begin()
        try:
            deposit = self._deposit(deposit_id)
            if not deposit.can_transition_to(DepositStatus.CONFIRMED):
                raise DepositAlreadyDecided(deposit.status.value)
            _assert_may_decide(deposit, actor)

            deposit.status = DepositStatus.CONFIRMED
            deposit.decided_at = when
            deposit.decided_by_actor_id = actor.principal_id
            deposit.version += 1

            if deposit.method is DepositMethod.EXCHANGE_OFFICE:
                # Only now does the money reach the bank — this is the step that makes
                # the earlier transfer more than a promise.
                entry = self._post(
                    EntryDraft(
                        reason=JournalReason.EXCHANGE_RECEIPT_VERIFIED,
                        occurred_at=when,
                        recorded_by_actor_id=actor.principal_id,
                        subject_kind="deposit",
                        subject_id=deposit.deposit_id,
                        memo=f"exchange receipt {deposit.reference} verified",
                    )
                    .debit(BANK, deposit.amount)
                    .credit(EXCHANGE_IN_TRANSIT, deposit.amount)
                )
                deposit.settled_entry_id = entry.entry_id
            else:
                # Hub cash and a direct bank transfer already sit where they belong; the
                # confirmation records that a second person agrees they arrived.
                deposit.settled_entry_id = deposit.submitted_entry_id

            self._uow.deposits.save(deposit)
            if deposit.settles_the_parcels_it_covers:
                self._mark_collections_settled(deposit, when)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return deposit

    def reject_deposit(
        self, *, deposit_id: UUID, actor, reason: str
    ) -> Deposit:
        """The receipt did not check out. The cash goes back into the driver's custody.

        Not a bookkeeping nicety: until someone finds where it went, the person who last
        physically had it is the driver, and the ledger should say so.
        """
        if not reason.strip():
            raise RejectionReasonRequired()
        self._uow.begin()
        try:
            deposit = self._deposit(deposit_id)
            if not deposit.can_transition_to(DepositStatus.REJECTED):
                raise DepositAlreadyDecided(deposit.status.value)
            _assert_may_decide(deposit, actor)

            moment = _now()
            deposit.status = DepositStatus.REJECTED
            deposit.decided_at = moment
            deposit.decided_by_actor_id = actor.principal_id
            deposit.rejection_reason = reason.strip()
            deposit.version += 1
            self._post(
                EntryDraft(
                    reason=JournalReason.CORRECTION,
                    occurred_at=moment,
                    recorded_by_actor_id=actor.principal_id,
                    subject_kind="deposit",
                    subject_id=deposit.deposit_id,
                    corrects_entry_id=deposit.submitted_entry_id,
                    memo=f"deposit {deposit.reference} rejected",
                )
                .debit(driver_custody(deposit.driver_principal_id), deposit.amount)
                .credit(_SUBMIT_DESTINATION[deposit.method], deposit.amount)
            )
            self._uow.deposits.save(deposit)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return deposit

    def settlement_history(
        self, *, driver_principal_id: UUID
    ) -> tuple[SettlementHistoryEntry, ...]:
        """DRV-A10 — every settlement with the receipt it was made against."""
        self._uow.begin()
        try:
            deposits = self._uow.deposits.list_for_driver(driver_principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(
            SettlementHistoryEntry(
                deposit_id=deposit.deposit_id,
                method=deposit.method,
                amount=deposit.amount,
                status=deposit.status,
                submitted_at=deposit.submitted_at,
                decided_at=deposit.decided_at,
                reference=deposit.reference,
                receipt=deposit.receipt,
            )
            for deposit in sorted(
                deposits,
                key=lambda d: d.submitted_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
        )

    def pending_verification(self) -> tuple[Deposit, ...]:
        self._uow.begin()
        try:
            found = self._uow.deposits.list_pending_verification()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def deposit(self, deposit_id: UUID) -> Deposit:
        self._uow.begin()
        try:
            found = self._deposit(deposit_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _post(self, draft: EntryDraft) -> JournalEntry:
        entry = draft.build(uuid4())
        self._uow.ledger.append(entry)
        return entry

    def _account(self, driver_principal_id: UUID) -> DriverCashAccount:
        account = self._uow.driver_accounts.find_for_driver(driver_principal_id)
        if account is None:
            raise DriverAccountNotFound(str(driver_principal_id))
        return account

    def _deposit(self, deposit_id: UUID) -> Deposit:
        deposit = self._uow.deposits.get(deposit_id)
        if deposit is None:
            raise DepositNotFound(str(deposit_id))
        return deposit

    def _held(self, driver_principal_id: UUID) -> Money:
        account = driver_custody(driver_principal_id)
        return balance_of(
            account, self._uow.ledger.entries_for_account(account)
        ).amount

    def _mark_collections_settled(self, deposit: Deposit, moment: datetime) -> None:
        """PAY-01 — cash reaching a hub cashier is what makes those parcels paid.

        Oldest first, and only up to what the deposit covers: a partial deposit settles
        the parcels it can pay for and leaves the rest outstanding, rather than marking
        everything paid because some money arrived.

        Each settled parcel publishes its fact here, inside the same transaction that
        settled it — so a rolled-back confirmation announces nothing.
        """
        remaining = deposit.amount
        outstanding = sorted(
            self._uow.collections.list_unsettled_for_driver(
                deposit.driver_principal_id
            ),
            key=lambda c: c.collected_at,
        )
        for collection in outstanding:
            if collection.total > remaining:
                break
            collection.settled_at = moment
            collection.settling_deposit_id = deposit.deposit_id
            collection.version += 1
            self._uow.collections.save(collection)
            remaining = remaining - collection.total

            payload, subject = build_cod_settled_envelope(
                collection=collection,
                aggregate_version=collection.version,
                event_id=uuid4(),
                correlation_id=uuid4(),
            )
            enqueue(
                self._uow,
                payload,
                subject,
                aggregate_id=collection.collection_id,
                aggregate_version=collection.version,
                max_attempts=self._outbox_max_attempts,
            )


#: Where the cash goes the moment it leaves the driver, per method. All three move it
#: out of custody at once — that is what frees the limit (DRV-A08, DRV-A09).
_SUBMIT_DESTINATION = {
    DepositMethod.HUB_CASHIER: HUB_CASH,
    DepositMethod.EXCHANGE_OFFICE: EXCHANGE_IN_TRANSIT,
    DepositMethod.BANK_TRANSFER: BANK,
}

_SUBMIT_REASON = {
    DepositMethod.HUB_CASHIER: JournalReason.DEPOSIT_TO_HUB_CASHIER,
    DepositMethod.EXCHANGE_OFFICE: JournalReason.DEPOSIT_VIA_EXCHANGE,
    DepositMethod.BANK_TRANSFER: JournalReason.DEPOSIT_BY_BANK_TRANSFER,
}


def _assert_may_decide(deposit: Deposit, actor) -> None:
    if deposit.method is DepositMethod.EXCHANGE_OFFICE:
        if not actor.may_verify_an_exchange_receipt:
            raise OnlyAnAccountantVerifiesAnExchangeReceipt()
        return
    if not actor.may_confirm_a_hub_deposit:
        raise OnlyACashierConfirmsAHubDeposit()


def unsettled_collections(
    unit_of_work: FinanceUnitOfWork, driver_principal_id: UUID
) -> tuple[CodCollection, ...]:
    return unit_of_work.collections.list_unsettled_for_driver(driver_principal_id)
