"""Finance aggregates: the driver's cash record, deposits, payouts and reconciliation.

The ledger in ``ledger.py`` is the record of what moved. These are the things people
interact with: a driver's cash-on-hand and their limit, a deposit waiting for an
accountant, a merchant's payout request, an end-of-route count that did not match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from finance.domain.money import Money
from finance.domain.value_objects import (
    DEPOSIT_TRANSITIONS,
    PAYOUT_TRANSITIONS,
    CodPaymentChannel,
    DepositMethod,
    DepositStatus,
    EvidenceMediaRef,
    PayoutMethod,
    PayoutStatus,
    ReconciliationOutcome,
    ReconciliationStatus,
)


@dataclass(slots=True)
class DriverCashAccount:
    """A driver's cash-on-hand and the limit on it (DRV-A05, DRV-A06).

    ``limit`` is per driver because v6.3 p.31 says the limit is per driver; there is a
    platform default in configuration and **no number invented here**.

    Held cash is not stored on this record — it is derived from the ledger, so it cannot
    drift from the postings that are meant to explain it. What this record holds is the
    driver's own policy: their limit, and whether they are allowed to take more COD.
    """

    account_id: UUID
    driver_principal_id: UUID
    limit: Money
    #: Set when operations suspends a driver from taking COD independently of the limit.
    cod_blocked: bool = False
    cod_blocked_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: int = 1

    def utilisation_percent(self, held: Money) -> int:
        """DRV-A06 — the app shows a percentage, so it is computed here, not there.

        Integer arithmetic on purpose: this is derived from money, and a float here
        would be the one place a rounding artefact could show a driver 99% when they are
        at their limit.
        """
        if self.limit.is_zero:
            return 100 if not held.is_zero else 0
        return (held.minor_units * 100) // self.limit.minor_units

    def headroom(self, held: Money) -> Money:
        """How much more this driver may take. Never negative."""
        if held >= self.limit:
            return Money.zero(self.limit.currency)
        return self.limit - held

    def is_over_limit(self, held: Money) -> bool:
        return held > self.limit

    def may_take_more_cod(self, held: Money) -> bool:
        """ADR-0012 — exceeding the limit blocks **assignment**, never delivery.

        A parcel already at a door is delivered and its cash collected whatever this
        says: physical delivery is irreversible (ADR-0003).
        """
        return not self.cod_blocked and held < self.limit


@dataclass(slots=True)
class CodCollection:
    """One parcel's COD, as Finance sees it (PAY-01).

    v6.3 p.33 draws the line this class exists to hold: cash is *collected* at the door
    but the parcel only counts as **paid** once that cash reaches the hub cashier. Card
    and online payments are paid the moment they are approved, because the money is
    already HUDHUD's.
    """

    collection_id: UUID
    tracking_code: str
    merchant_id: UUID
    channel: CodPaymentChannel
    goods_amount: Money
    delivery_fee: Money
    collected_at: datetime
    driver_principal_id: UUID | None = None
    #: Set when the cash reaches a hub cashier, or immediately for card and online.
    settled_at: datetime | None = None
    settling_deposit_id: UUID | None = None
    journal_entry_id: UUID | None = None
    version: int = 1

    @property
    def total(self) -> Money:
        return self.goods_amount + self.delivery_fee

    @property
    def enters_driver_custody(self) -> bool:
        """Only cash does. A POS card payment is HUDHUD's before the driver moves."""
        return self.channel is CodPaymentChannel.CASH

    @property
    def is_paid(self) -> bool:
        """PAY-01 — the whole rule, in one place."""
        if self.channel in {
            CodPaymentChannel.ONLINE,
            CodPaymentChannel.POS_CARD,
            CodPaymentChannel.AT_HUB_IN_PERSON,
        }:
            return True
        return self.settled_at is not None


@dataclass(slots=True)
class Deposit:
    """A driver getting cash out of their own custody (DRV-A07 … DRV-A10).

    All three methods require an amount, a reference and a receipt: Driver App v8's
    `deposit` screen disables submission without them, and an unreferenced deposit cannot
    be reconciled against anything.
    """

    deposit_id: UUID
    driver_principal_id: UUID
    method: DepositMethod
    amount: Money
    reference: str
    receipt: EvidenceMediaRef
    status: DepositStatus = DepositStatus.PENDING_VERIFICATION
    submitted_at: datetime | None = None
    #: Which hub took the cash. Only meaningful for a hub-cashier deposit.
    hub_id: UUID | None = None
    decided_at: datetime | None = None
    decided_by_actor_id: UUID | None = None
    rejection_reason: str | None = None
    #: The entry that moved the money out of custody when it was submitted.
    submitted_entry_id: UUID | None = None
    #: The entry that landed it in the bank, once verified.
    settled_entry_id: UUID | None = None
    version: int = 1

    def can_transition_to(self, target: DepositStatus) -> bool:
        return target in DEPOSIT_TRANSITIONS[self.status]

    @property
    def is_open(self) -> bool:
        return self.status is DepositStatus.PENDING_VERIFICATION

    @property
    def frees_the_limit_immediately(self) -> bool:
        """DRV-A08, DRV-A09 — v6.3 p.31: the driver keeps collecting straight away.

        True for every method, because in all three the cash has physically left the
        driver. What differs is where it has gone: a hub cashier's confirmation is what
        makes a COD parcel *paid*, while an exchange transfer explicitly is not (p.33).
        """
        return True

    @property
    def settles_the_parcels_it_covers(self) -> bool:
        """PAY-04 — only cash reaching a hub cashier confirms the original payment.

        v6.3 p.33: "cash-transfer-via-exchange-office is not a valid way to confirm the
        original payment — only to settle cash already collected."
        """
        return self.method is DepositMethod.HUB_CASHIER


@dataclass(slots=True)
class PayoutRequest:
    """A merchant asking to be paid what HUDHUD owes them (PAY-06).

    The four methods are v6.3's. **The operating procedure for each is an Open Item**
    (PAY-07): this models the request, its method, its destination and its state, and
    refuses to invent a fee, a timing rule or a reconciliation step for any of them.
    """

    payout_id: UUID
    merchant_id: UUID
    method: PayoutMethod
    amount: Money
    status: PayoutStatus = PayoutStatus.REQUESTED
    #: An IBAN, a card reference, an exchange office, or the hub to collect from. Opaque
    #: to Finance: what a valid destination looks like per method is part of PAY-07.
    destination_reference: str | None = None
    requested_at: datetime | None = None
    requested_by_principal_id: UUID | None = None
    decided_at: datetime | None = None
    decided_by_actor_id: UUID | None = None
    rejection_reason: str | None = None
    paid_at: datetime | None = None
    paid_entry_id: UUID | None = None
    version: int = 1

    def can_transition_to(self, target: PayoutStatus) -> bool:
        return target in PAYOUT_TRANSITIONS[self.status]

    @property
    def is_open(self) -> bool:
        return self.status in {PayoutStatus.REQUESTED, PayoutStatus.APPROVED}


@dataclass(slots=True)
class RouteReconciliation:
    """The end-of-route count (DRV-A12, DRV-A13).

    v6.3 p.31 and p.33: what the driver hands over is checked against what was expected,
    and **a mismatch is investigated before payout**. That last clause is why an open
    reconciliation is a real thing and not just a log line.
    """

    reconciliation_id: UUID
    driver_principal_id: UUID
    route_day: date
    expected: Money
    counted: Money
    outcome: ReconciliationOutcome
    status: ReconciliationStatus = ReconciliationStatus.OPEN
    parcels_returned_confirmed: bool = False
    cash_settled_confirmed: bool = False
    opened_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by_actor_id: UUID | None = None
    resolution_note: str | None = None
    adjustment_entry_id: UUID | None = None
    version: int = 1

    @property
    def difference(self) -> Money:
        """How far out the count was. Always a magnitude; ``outcome`` carries the sign."""
        if self.counted >= self.expected:
            return self.counted - self.expected
        return self.expected - self.counted

    @property
    def is_balanced(self) -> bool:
        return self.outcome is ReconciliationOutcome.BALANCED

    @property
    def blocks_payout(self) -> bool:
        """v6.3 p.31, p.33 — an unresolved mismatch stops the money going further."""
        return not self.is_balanced and self.status is not ReconciliationStatus.RESOLVED

    @property
    def day_is_complete(self) -> bool:
        """DRV-A12 — both halves confirmed: parcels handed over *and* cash settled."""
        return self.parcels_returned_confirmed and self.cash_settled_confirmed


@dataclass(slots=True)
class MerchantAccount:
    """A merchant's running balance (PAY-05).

    Like the driver's cash, the balance itself is derived from the ledger. What lives
    here is the merchant's own settlement policy and whether anything is holding their
    money up.
    """

    account_id: UUID
    merchant_id: UUID
    payouts_blocked: bool = False
    payouts_blocked_reason: str | None = None
    created_at: datetime | None = None
    version: int = 1


@dataclass(frozen=True, slots=True)
class CashExposure:
    """OPS-04 — who is holding how much, and who is over their limit."""

    driver_principal_id: UUID
    held: Money
    limit: Money
    utilisation_percent: int
    over_limit: bool
    open_deposits: int = 0


@dataclass(frozen=True, slots=True)
class SettlementHistoryEntry:
    """DRV-A10 — one row of the driver's settlement history, with its receipt."""

    deposit_id: UUID
    method: DepositMethod
    amount: Money
    status: DepositStatus
    submitted_at: datetime | None
    decided_at: datetime | None
    reference: str
    receipt: EvidenceMediaRef


@dataclass(frozen=True, slots=True)
class RefusalCharge:
    """PAY-08 — what a refusal costs the merchant.

    v6.3 p.38 and p.39 are unambiguous about the charge: the merchant pays **both** the
    return-trip fee and the original delivery fee, regardless of the reason. What is an
    Open Item is whether HUDHUD may *waive* the return-trip fee for a merchant, so the
    charge is implemented and the waiver is refused.
    """

    tracking_code: str
    merchant_id: UUID
    delivery_fee: Money
    return_trip_fee: Money
    tariff: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> Money:
        return self.delivery_fee + self.return_trip_fee
