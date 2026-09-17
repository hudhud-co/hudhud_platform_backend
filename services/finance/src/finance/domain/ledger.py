"""The double-entry ledger (ADR-0012).

One rule holds this file together: **a journal entry balances or it does not exist.**
Every movement of money is at least two postings whose debits equal their credits, the
entries are append-only, and a mistake is corrected by a compensating entry rather than
by editing the original. That is what makes the COD story auditable, which is the whole
reason ADR-0005 was superseded.

Balances are derived by summing postings, not stored as a running total that could drift
from the entries that are supposed to explain it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from finance.domain.money import Currency, Money
from finance.domain.value_objects import (
    AccountKind,
    AccountRef,
    JournalReason,
    Side,
)


class LedgerError(Exception):
    """Base class for every ledger rule."""


class UnbalancedEntry(LedgerError):
    def __init__(self, debits: int, credits: int) -> None:
        self.debits = debits
        self.credits = credits
        super().__init__(
            f"journal entry does not balance: debits {debits} ≠ credits {credits}"
        )


class EmptyEntry(LedgerError):
    def __init__(self) -> None:
        super().__init__("a journal entry needs at least two postings")


class SingleSidedEntry(LedgerError):
    def __init__(self, side: str) -> None:
        super().__init__(f"a journal entry cannot be all {side}")


class MixedCurrencyEntry(LedgerError):
    def __init__(self, currencies: Sequence[str]) -> None:
        super().__init__(f"one entry, one currency — found {sorted(currencies)}")


class ZeroPosting(LedgerError):
    def __init__(self) -> None:
        super().__init__("a posting of zero moves nothing and is not recorded")


@dataclass(frozen=True, slots=True)
class Posting:
    """One side of one entry. Immutable, like the entry that holds it."""

    account: AccountRef
    side: Side
    amount: Money

    def __post_init__(self) -> None:
        if self.amount.is_zero:
            raise ZeroPosting()

    @property
    def signed_minor_units(self) -> int:
        """The effect on this account's own balance, in its natural direction.

        A debit increases an asset and decreases a liability; a credit does the reverse.
        Expressing it this way means a balance is a plain sum with no per-account
        special-casing at the call site.
        """
        increases = (
            self.side is Side.DEBIT
            if self.account.is_debit_normal
            else self.side is Side.CREDIT
        )
        return self.amount.minor_units if increases else -self.amount.minor_units


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """A balanced set of postings, recorded once and never changed."""

    entry_id: UUID
    reason: JournalReason
    postings: tuple[Posting, ...]
    occurred_at: datetime
    recorded_by_actor_id: UUID | None = None
    #: What in the world this entry is about — a stop, a deposit, a payout.
    subject_kind: str | None = None
    subject_id: UUID | None = None
    #: The client key this entry was created under, so a retry finds it again.
    idempotency_key: str | None = None
    #: Set on a correcting entry to name the one it corrects.
    corrects_entry_id: UUID | None = None
    memo: str | None = None

    def __post_init__(self) -> None:
        if len(self.postings) < 2:
            raise EmptyEntry()
        currencies = {posting.amount.currency.value for posting in self.postings}
        if len(currencies) > 1:
            raise MixedCurrencyEntry(sorted(currencies))
        debits = sum(
            p.amount.minor_units for p in self.postings if p.side is Side.DEBIT
        )
        credits = sum(
            p.amount.minor_units for p in self.postings if p.side is Side.CREDIT
        )
        if debits == 0:
            raise SingleSidedEntry("credits")
        if credits == 0:
            raise SingleSidedEntry("debits")
        if debits != credits:
            raise UnbalancedEntry(debits, credits)

    @property
    def currency(self) -> Currency:
        return self.postings[0].amount.currency

    @property
    def total(self) -> Money:
        """The size of the entry: one side of it, since both sides are equal."""
        return Money(
            minor_units=sum(
                p.amount.minor_units for p in self.postings if p.side is Side.DEBIT
            ),
            currency=self.currency,
        )

    def touches(self, account: AccountRef) -> bool:
        return any(posting.account == account for posting in self.postings)


@dataclass(frozen=True, slots=True)
class Balance:
    """An account's balance, derived from its postings rather than stored."""

    account: AccountRef
    amount: Money
    posting_count: int = 0

    @property
    def is_zero(self) -> bool:
        return self.amount.is_zero


def balance_of(
    account: AccountRef,
    entries: Iterable[JournalEntry],
    *,
    currency: Currency = Currency.IQD,
) -> Balance:
    """Sum an account's postings across entries.

    The result is always non-negative because :class:`Money` refuses a negative amount —
    which is the right constraint here: a driver cannot hold minus cash, and a merchant
    payable that went negative would mean HUDHUD had paid out more than it owed. A caller
    that could produce one is doing something the ledger should refuse, so
    :func:`assert_covers` exists to check before posting rather than after.
    """
    total = 0
    count = 0
    for entry in entries:
        for posting in entry.postings:
            if posting.account != account:
                continue
            total += posting.signed_minor_units
            count += 1
    if total < 0:
        msg = (
            f"{account.kind.value} balance went negative ({total}); "
            "a posting was made that the ledger should have refused"
        )
        raise LedgerError(msg)
    return Balance(
        account=account,
        amount=Money(minor_units=total, currency=currency),
        posting_count=count,
    )


@dataclass(slots=True)
class EntryDraft:
    """A journal entry under construction.

    Exists so a service can describe a movement in the language of ADR-0012 — "debit the
    driver's custody, credit the merchant and the revenue" — and have the balance checked
    once, at the end, by :class:`JournalEntry` itself.
    """

    reason: JournalReason
    occurred_at: datetime
    postings: list[Posting] = field(default_factory=list)
    recorded_by_actor_id: UUID | None = None
    subject_kind: str | None = None
    subject_id: UUID | None = None
    idempotency_key: str | None = None
    corrects_entry_id: UUID | None = None
    memo: str | None = None

    def debit(self, account: AccountRef, amount: Money) -> EntryDraft:
        self.postings.append(Posting(account=account, side=Side.DEBIT, amount=amount))
        return self

    def credit(self, account: AccountRef, amount: Money) -> EntryDraft:
        self.postings.append(Posting(account=account, side=Side.CREDIT, amount=amount))
        return self

    def build(self, entry_id: UUID) -> JournalEntry:
        return JournalEntry(
            entry_id=entry_id,
            reason=self.reason,
            postings=tuple(self.postings),
            occurred_at=self.occurred_at,
            recorded_by_actor_id=self.recorded_by_actor_id,
            subject_kind=self.subject_kind,
            subject_id=self.subject_id,
            idempotency_key=self.idempotency_key,
            corrects_entry_id=self.corrects_entry_id,
            memo=self.memo,
        )


def reverse(
    entry: JournalEntry, *, entry_id: UUID, occurred_at: datetime, actor_id: UUID | None
) -> JournalEntry:
    """The compensating entry for a mistaken one (ADR-0012 rollback).

    Every posting keeps its account and amount and swaps its side, so the pair sums to
    nothing and both remain on the record. Nothing is deleted and nothing is edited.
    """
    flipped = tuple(
        Posting(
            account=posting.account,
            side=Side.CREDIT if posting.side is Side.DEBIT else Side.DEBIT,
            amount=posting.amount,
        )
        for posting in entry.postings
    )
    return JournalEntry(
        entry_id=entry_id,
        reason=JournalReason.CORRECTION,
        postings=flipped,
        occurred_at=occurred_at,
        recorded_by_actor_id=actor_id,
        subject_kind=entry.subject_kind,
        subject_id=entry.subject_id,
        corrects_entry_id=entry.entry_id,
        memo=f"reverses {entry.reason.value}",
    )


def driver_custody(driver_principal_id: UUID) -> AccountRef:
    return AccountRef(
        kind=AccountKind.DRIVER_CASH_CUSTODY, party_id=driver_principal_id
    )


def merchant_payable(merchant_id: UUID) -> AccountRef:
    return AccountRef(kind=AccountKind.MERCHANT_PAYABLE, party_id=merchant_id)


def receiver_refund_payable(receiver_principal_id: UUID) -> AccountRef:
    return AccountRef(
        kind=AccountKind.RECEIVER_REFUND_PAYABLE, party_id=receiver_principal_id
    )


HUB_CASH = AccountRef(kind=AccountKind.HUB_CASH)
EXCHANGE_IN_TRANSIT = AccountRef(kind=AccountKind.EXCHANGE_IN_TRANSIT)
BANK = AccountRef(kind=AccountKind.BANK)
HUDHUD_REVENUE = AccountRef(kind=AccountKind.HUDHUD_REVENUE)
