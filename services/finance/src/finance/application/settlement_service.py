"""The merchant's side: their balance, their payouts, the refusal charge, and the
end-of-route count that has to come out right before any of it moves.

Two v6.3 Open Items land here and both are held open in the narrowest possible way.

**PAY-07** — the procedure per payout method needs an accountant. A merchant can request
a payout, operations can approve or reject it, and the whole state machine works. What
cannot happen is the money moving, because paying out means applying a fee schedule and a
timing rule that nobody has written down.

**PAY-08** — a refusal charges the merchant both the return-trip fee and the original
delivery fee, regardless of reason (p.38, p.39). That much is settled and is implemented.
What is undecided is whether HUDHUD may *waive* the return-trip fee, so the waiver is the
one thing refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from finance.application.publishing import enqueue
from finance.domain.entities import (
    MerchantAccount,
    PayoutRequest,
    RefusalCharge,
    RouteReconciliation,
)
from finance.domain.errors import (
    InsufficientMerchantBalance,
    OnlyOperationsApprovesAPayout,
    OnlyOperationsResolvesAMismatch,
    PayoutDestinationRequired,
    PayoutNotFound,
    PayoutProcedureNotDefined,
    PayoutsBlockedForThisMerchant,
    PayoutTransitionNotAllowed,
    ReconciliationAlreadyResolved,
    ReconciliationNotFound,
    RejectionReasonRequired,
    ResolutionNoteRequired,
    ReturnFeeWaiverNotDecided,
    TariffNotConfigured,
    UnresolvedReconciliationBlocksPayout,
)
from finance.domain.ledger import (
    HUDHUD_REVENUE,
    EntryDraft,
    JournalEntry,
    balance_of,
    driver_custody,
    merchant_payable,
)
from finance.domain.money import Money
from finance.domain.value_objects import (
    JournalReason,
    PayoutMethod,
    PayoutStatus,
    ReconciliationOutcome,
    ReconciliationStatus,
)
from finance.infrastructure.contracts.envelopes import (
    build_payout_decided_envelope,
    build_payout_requested_envelope,
)
from finance.ports.repository import FinanceUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class MerchantBalance:
    """PAY-05 — what the merchant is owed, and what is holding it up."""

    merchant_id: UUID
    balance: Money
    payouts_blocked: bool
    payouts_blocked_reason: str | None
    open_payout_count: int
    #: What is requested or approved but not yet paid, so the two figures agree.
    committed: Money

    @property
    def available(self) -> Money:
        if self.committed >= self.balance:
            return Money.zero(self.balance.currency)
        return self.balance - self.committed


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    reconciliation: RouteReconciliation
    adjustment: JournalEntry | None


class SettlementService:
    def __init__(
        self,
        unit_of_work: FinanceUnitOfWork,
        *,
        return_trip_fee: Money | None = None,
        return_fee_waiver_permitted: bool = False,
        outbox_max_attempts: int = 5,
    ) -> None:
        self._uow = unit_of_work
        # PAY-08 — the amount is a tariff, not a constant. With none configured the
        # charge is refused rather than guessed.
        self._return_trip_fee = return_trip_fee
        # PAY-08's Open Item, as a switch that is **off** and has no safe default on.
        # Turning it on is the business deciding that HUDHUD may waive the fee; until
        # then :meth:`waive_return_trip_fee` refuses.
        self._waiver_permitted = return_fee_waiver_permitted
        self._outbox_max_attempts = outbox_max_attempts

    @property
    def return_fee_waiver_permitted(self) -> bool:
        return self._waiver_permitted

    # ------------------------------------------------------------------ PAY-05

    def open_merchant_account(self, *, merchant_id: UUID) -> MerchantAccount:
        self._uow.begin()
        try:
            existing = self._uow.merchant_accounts.find_for_merchant(merchant_id)
            if existing is not None:
                self._uow.commit()
                return existing
            account = MerchantAccount(
                account_id=uuid4(), merchant_id=merchant_id, created_at=_now()
            )
            self._uow.merchant_accounts.save(account)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return account

    def balance_for(self, *, merchant_id: UUID) -> MerchantBalance:
        """Derived from the ledger, never from a stored running total.

        A stored total is a second source of truth for the same fact, and the two drift.
        """
        self._uow.begin()
        try:
            account = self._merchant_account(merchant_id)
            payable = merchant_payable(merchant_id)
            balance = balance_of(
                payable, self._uow.ledger.entries_for_account(payable)
            ).amount
            open_payouts = [
                payout
                for payout in self._uow.payouts.list_for_merchant(merchant_id)
                if payout.is_open
            ]
            committed = Money(
                minor_units=sum(p.amount.minor_units for p in open_payouts),
                currency=balance.currency,
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return MerchantBalance(
            merchant_id=merchant_id,
            balance=balance,
            payouts_blocked=account.payouts_blocked,
            payouts_blocked_reason=account.payouts_blocked_reason,
            open_payout_count=len(open_payouts),
            committed=committed,
        )

    # ------------------------------------------------------------------ PAY-06

    def request_payout(
        self,
        *,
        merchant_id: UUID,
        method: PayoutMethod,
        amount: Money,
        destination_reference: str | None = None,
        requested_by_principal_id: UUID | None = None,
    ) -> PayoutRequest:
        """A merchant asking for their money, by one of v6.3's four methods.

        Everything about the request is real: the method, where it goes, the amount
        checked against what is actually available, and the state it sits in. Only
        :meth:`pay_payout` is blocked by PAY-07.
        """
        if method is not PayoutMethod.IN_PERSON_AT_HUB and not (
            destination_reference and destination_reference.strip()
        ):
            # Collecting in person needs no destination; the other three do, or nobody
            # knows where to send it.
            raise PayoutDestinationRequired(method.value)

        current = self.balance_for(merchant_id=merchant_id)
        if current.payouts_blocked:
            raise PayoutsBlockedForThisMerchant(
                current.payouts_blocked_reason or "on hold"
            )
        if amount > current.available:
            raise InsufficientMerchantBalance(
                amount.minor_units, current.available.minor_units
            )

        self._uow.begin()
        try:
            payout = PayoutRequest(
                payout_id=uuid4(),
                merchant_id=merchant_id,
                method=method,
                amount=amount,
                status=PayoutStatus.REQUESTED,
                destination_reference=(
                    destination_reference.strip() if destination_reference else None
                ),
                requested_at=_now(),
                requested_by_principal_id=requested_by_principal_id,
            )
            self._uow.payouts.save(payout)
            payload, subject = build_payout_requested_envelope(
                payout=payout,
                aggregate_version=payout.version,
                event_id=uuid4(),
                correlation_id=uuid4(),
            )
            enqueue(
                self._uow,
                payload,
                subject,
                aggregate_id=payout.payout_id,
                aggregate_version=payout.version,
                max_attempts=self._outbox_max_attempts,
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return payout

    def approve_payout(self, *, payout_id: UUID, actor) -> PayoutRequest:
        """ADR-0012 — only Operations."""
        if not actor.may_approve_a_payout:
            raise OnlyOperationsApprovesAPayout()
        return self._decide(
            payout_id=payout_id, target=PayoutStatus.APPROVED, actor=actor
        )

    def reject_payout(self, *, payout_id: UUID, actor, reason: str) -> PayoutRequest:
        if not actor.may_approve_a_payout:
            raise OnlyOperationsApprovesAPayout()
        if not reason.strip():
            raise RejectionReasonRequired()
        return self._decide(
            payout_id=payout_id,
            target=PayoutStatus.REJECTED,
            actor=actor,
            reason=reason.strip(),
        )

    def pay_payout(self, *, payout_id: UUID, actor) -> PayoutRequest:
        """PAY-07 — refused, and this is the only thing that is.

        v6.3 Appendix A p.44 leaves the operating procedure per payout method to an
        accountant: what fee applies, when the money lands, and how the transfer is
        reconciled. Paying one out means choosing all three. The request, the approval
        and the rejection are all implemented and tested; only this step stops.
        """
        if not actor.may_approve_a_payout:
            raise OnlyOperationsApprovesAPayout()
        self._uow.begin()
        try:
            payout = self._payout(payout_id)
            if not payout.can_transition_to(PayoutStatus.PAID):
                raise PayoutTransitionNotAllowed(
                    payout.status.value, PayoutStatus.PAID.value
                )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        # Everything that can be checked has been checked. What remains is the decision
        # nobody has made.
        raise PayoutProcedureNotDefined(payout.method.value)

    def payouts_for(self, *, merchant_id: UUID) -> tuple[PayoutRequest, ...]:
        self._uow.begin()
        try:
            found = self._uow.payouts.list_for_merchant(merchant_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def open_payouts(self) -> tuple[PayoutRequest, ...]:
        self._uow.begin()
        try:
            found = self._uow.payouts.list_open()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------------ PAY-08

    def charge_for_refusal(
        self,
        *,
        tracking_code: str,
        merchant_id: UUID,
        delivery_fee: Money,
        actor_id: UUID | None = None,
    ) -> RefusalCharge:
        """v6.3 p.38, p.39 — both fees, regardless of reason.

        "Regardless of reason" is the part that is settled, and it is why this method
        takes no reason argument: there is no reason that changes the answer, so
        accepting one would imply there might be.
        """
        if self._return_trip_fee is None:
            raise TariffNotConfigured("return-trip fee")
        charge = RefusalCharge(
            tracking_code=tracking_code,
            merchant_id=merchant_id,
            delivery_fee=delivery_fee,
            return_trip_fee=self._return_trip_fee,
        )
        self._uow.begin()
        try:
            moment = _now()
            entry = (
                EntryDraft(
                    reason=JournalReason.RETURN_TRIP_FEE_CHARGED,
                    occurred_at=moment,
                    recorded_by_actor_id=actor_id,
                    subject_kind="refusal",
                    subject_id=None,
                    memo=f"refusal charge {tracking_code}",
                )
                # What HUDHUD owes the merchant falls by both fees; HUDHUD earns them.
                .debit(merchant_payable(merchant_id), charge.total)
                .credit(HUDHUD_REVENUE, charge.total)
                .build(uuid4())
            )
            self._uow.ledger.append(entry)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return charge

    def waive_return_trip_fee(
        self,
        *,
        tracking_code: str,
        merchant_id: UUID,
        amount: Money,
        actor,
        note: str,
    ) -> JournalEntry:
        """PAY-08 — refused while the Open Item stands, and only this.

        Whether HUDHUD may waive the return-trip fee for a merchant is undecided (v6.3
        Appendix A p.44). The **charge** is not: p.38 and p.39 say a refusal charges the
        merchant both fees regardless of reason, and :meth:`charge_for_refusal`
        implements exactly that.

        So the waiver is written, in full, behind one configuration flag that is off and
        has no safe default on. The day an accountant decides HUDHUD may waive, setting
        ``FINANCE_RETURN_FEE_WAIVER_PERMITTED`` opens it with no code change — and if
        they decide it may not, this method and its flag come out together.
        """
        if not self._waiver_permitted:
            raise ReturnFeeWaiverNotDecided()
        if not actor.may_approve_a_payout:
            # A waiver is money given back, so it needs the same authority a payout does.
            raise OnlyOperationsApprovesAPayout()
        if not note.strip():
            raise ResolutionNoteRequired()

        self._uow.begin()
        try:
            entry = (
                EntryDraft(
                    reason=JournalReason.CORRECTION,
                    occurred_at=_now(),
                    recorded_by_actor_id=actor.principal_id,
                    subject_kind="refusal_waiver",
                    subject_id=merchant_id,
                    memo=f"return-trip fee waived for {tracking_code}: {note.strip()}",
                )
                # The reverse of the charge: HUDHUD gives up the revenue and the
                # merchant's payable is restored by the same amount.
                .debit(HUDHUD_REVENUE, amount)
                .credit(merchant_payable(merchant_id), amount)
                .build(uuid4())
            )
            self._uow.ledger.append(entry)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return entry

    # ------------------------------------------------------- DRV-A12, DRV-A13

    def open_reconciliation(
        self,
        *,
        driver_principal_id: UUID,
        route_day: date,
        counted: Money,
        parcels_returned_confirmed: bool = False,
    ) -> RouteReconciliation:
        """DRV-A13 — what was handed over, against what the ledger expected.

        ``expected`` is not taken from the caller: it is what the driver's custody
        account says they are holding. A figure supplied alongside the count would let
        the two agree by construction, which is the one thing this must not do.
        """
        self._uow.begin()
        try:
            custody = driver_custody(driver_principal_id)
            expected = balance_of(
                custody, self._uow.ledger.entries_for_account(custody)
            ).amount
            if counted == expected:
                outcome = ReconciliationOutcome.BALANCED
            elif counted < expected:
                outcome = ReconciliationOutcome.SHORT
            else:
                outcome = ReconciliationOutcome.OVER

            reconciliation = RouteReconciliation(
                reconciliation_id=uuid4(),
                driver_principal_id=driver_principal_id,
                route_day=route_day,
                expected=expected,
                counted=counted,
                outcome=outcome,
                status=(
                    ReconciliationStatus.RESOLVED
                    if outcome is ReconciliationOutcome.BALANCED
                    else ReconciliationStatus.OPEN
                ),
                parcels_returned_confirmed=parcels_returned_confirmed,
                cash_settled_confirmed=outcome is ReconciliationOutcome.BALANCED,
                opened_at=_now(),
                resolved_at=(
                    _now() if outcome is ReconciliationOutcome.BALANCED else None
                ),
            )
            self._uow.reconciliations.save(reconciliation)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return reconciliation

    def confirm_hub_return(
        self, *, reconciliation_id: UUID, parcels: bool = True, cash: bool = True
    ) -> RouteReconciliation:
        """DRV-A12 — both halves of the end-of-day return, each confirmed separately."""
        self._uow.begin()
        try:
            reconciliation = self._reconciliation(reconciliation_id)
            if parcels:
                reconciliation.parcels_returned_confirmed = True
            if cash:
                reconciliation.cash_settled_confirmed = True
            reconciliation.version += 1
            self._uow.reconciliations.save(reconciliation)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return reconciliation

    def resolve_reconciliation(
        self, *, reconciliation_id: UUID, actor, note: str, post_adjustment: bool = True
    ) -> ReconciliationResult:
        """Close a mismatch, with the ledger brought into line with the count.

        Without the adjustment the driver's custody would keep showing money that is not
        there, and every later limit check would be wrong by the same amount.
        """
        if not actor.may_resolve_a_mismatch:
            raise OnlyOperationsResolvesAMismatch()
        if not note.strip():
            raise ResolutionNoteRequired()

        self._uow.begin()
        try:
            reconciliation = self._reconciliation(reconciliation_id)
            if reconciliation.status is ReconciliationStatus.RESOLVED:
                raise ReconciliationAlreadyResolved()

            adjustment = None
            if post_adjustment and not reconciliation.is_balanced:
                custody = driver_custody(reconciliation.driver_principal_id)
                difference = reconciliation.difference
                draft = EntryDraft(
                    reason=JournalReason.RECONCILIATION_ADJUSTMENT,
                    occurred_at=_now(),
                    recorded_by_actor_id=actor.principal_id,
                    subject_kind="reconciliation",
                    subject_id=reconciliation.reconciliation_id,
                    memo=f"{reconciliation.outcome.value} on {reconciliation.route_day}",
                )
                if reconciliation.outcome is ReconciliationOutcome.SHORT:
                    # Less cash than the ledger says: custody falls, and the shortfall
                    # is HUDHUD's loss until somebody finds it.
                    draft.credit(custody, difference).debit(HUDHUD_REVENUE, difference)
                else:
                    # More cash than expected. It is HUDHUD's until explained.
                    draft.debit(custody, difference).credit(HUDHUD_REVENUE, difference)
                adjustment = draft.build(uuid4())
                self._uow.ledger.append(adjustment)
                reconciliation.adjustment_entry_id = adjustment.entry_id

            reconciliation.status = ReconciliationStatus.RESOLVED
            reconciliation.resolved_at = _now()
            reconciliation.resolved_by_actor_id = actor.principal_id
            reconciliation.resolution_note = note.strip()
            reconciliation.cash_settled_confirmed = True
            reconciliation.version += 1
            self._uow.reconciliations.save(reconciliation)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return ReconciliationResult(
            reconciliation=reconciliation, adjustment=adjustment
        )

    def unresolved_reconciliations(self) -> tuple[RouteReconciliation, ...]:
        self._uow.begin()
        try:
            found = self._uow.reconciliations.list_unresolved()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def assert_no_unresolved_mismatch(self, *, driver_principal_id: UUID) -> None:
        """v6.3 p.31, p.33 — "investigated before payout"."""
        self._uow.begin()
        try:
            open_ones = [
                r
                for r in self._uow.reconciliations.list_unresolved_for_driver(
                    driver_principal_id
                )
                if r.blocks_payout
            ]
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if open_ones:
            raise UnresolvedReconciliationBlocksPayout(len(open_ones))

    # ------------------------------------------------------------- internals

    def _decide(
        self, *, payout_id: UUID, target: PayoutStatus, actor, reason: str | None = None
    ) -> PayoutRequest:
        self._uow.begin()
        try:
            payout = self._payout(payout_id)
            if not payout.can_transition_to(target):
                raise PayoutTransitionNotAllowed(payout.status.value, target.value)
            payout.status = target
            payout.decided_at = _now()
            payout.decided_by_actor_id = actor.principal_id
            payout.rejection_reason = reason
            payout.version += 1
            self._uow.payouts.save(payout)
            payload, subject = build_payout_decided_envelope(
                payout=payout,
                aggregate_version=payout.version,
                event_id=uuid4(),
                correlation_id=uuid4(),
            )
            enqueue(
                self._uow,
                payload,
                subject,
                aggregate_id=payout.payout_id,
                aggregate_version=payout.version,
                max_attempts=self._outbox_max_attempts,
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return payout

    def _payout(self, payout_id: UUID) -> PayoutRequest:
        payout = self._uow.payouts.get(payout_id)
        if payout is None:
            raise PayoutNotFound(str(payout_id))
        return payout

    def _merchant_account(self, merchant_id: UUID) -> MerchantAccount:
        """The merchant's settlement *policy*, defaulting to "nothing is blocked".

        A merchant has a balance the moment COD is collected for them, whether or not
        anyone has opened an account row — the balance lives in the ledger, and this
        record only carries whether payouts are on hold. Refusing to report a balance
        because no policy row exists would 404 every merchant who has been paid but
        never administered, which is all of them until someone intervenes.

        The row is still created by :meth:`open_merchant_account` when operations needs
        to set policy, and :meth:`block_payouts` requires one, because a block is a
        deliberate act that has to be recorded.
        """
        account = self._uow.merchant_accounts.find_for_merchant(merchant_id)
        if account is None:
            return MerchantAccount(
                account_id=uuid4(), merchant_id=merchant_id, payouts_blocked=False
            )
        return account

    def block_payouts(
        self, *, merchant_id: UUID, reason: str, blocked: bool = True
    ) -> MerchantAccount:
        """Put a merchant's payouts on hold. Unlike a balance, this must be recorded."""
        if blocked and not reason.strip():
            raise ResolutionNoteRequired()
        self._uow.begin()
        try:
            account = self._uow.merchant_accounts.find_for_merchant(merchant_id)
            if account is None:
                account = MerchantAccount(
                    account_id=uuid4(), merchant_id=merchant_id, created_at=_now()
                )
            else:
                account.version += 1
            account.payouts_blocked = blocked
            account.payouts_blocked_reason = reason.strip() if blocked else None
            self._uow.merchant_accounts.save(account)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return account

    def _reconciliation(self, reconciliation_id: UUID) -> RouteReconciliation:
        found = self._uow.reconciliations.get(reconciliation_id)
        if found is None:
            raise ReconciliationNotFound(str(reconciliation_id))
        return found
