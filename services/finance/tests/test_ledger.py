"""The double-entry ledger itself (ADR-0012).

Everything else in this service leans on one property: **an entry balances or it does not
exist.** These tests attack that property directly, because if it holds, a wrong balance
elsewhere can only come from a wrong posting, not from a lost one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from finance_fixtures import iqd

from finance.domain.ledger import (
    BANK,
    EXCHANGE_IN_TRANSIT,
    HUB_CASH,
    HUDHUD_REVENUE,
    EmptyEntry,
    EntryDraft,
    JournalEntry,
    LedgerError,
    MixedCurrencyEntry,
    Posting,
    SingleSidedEntry,
    UnbalancedEntry,
    ZeroPosting,
    balance_of,
    driver_custody,
    merchant_payable,
    reverse,
)
from finance.domain.money import Currency, CurrencyMismatch, Money, NegativeAmount
from finance.domain.value_objects import (
    AccountKind,
    AccountRef,
    JournalReason,
    Side,
)


def now():
    return datetime.now(tz=UTC)


def draft(reason=JournalReason.COD_CASH_COLLECTED) -> EntryDraft:
    return EntryDraft(reason=reason, occurred_at=now())


# ------------------------------------------------------------------ balance


def test_an_entry_must_balance() -> None:
    with pytest.raises(UnbalancedEntry):
        draft().debit(BANK, iqd(100)).credit(HUDHUD_REVENUE, iqd(99)).build(uuid4())


def test_an_entry_needs_two_sides() -> None:
    with pytest.raises(EmptyEntry):
        draft().debit(BANK, iqd(100)).build(uuid4())


def test_an_entry_cannot_be_all_debits() -> None:
    with pytest.raises(SingleSidedEntry):
        draft().debit(BANK, iqd(100)).debit(HUB_CASH, iqd(100)).build(uuid4())


def test_an_entry_cannot_be_all_credits() -> None:
    with pytest.raises(SingleSidedEntry):
        draft().credit(HUDHUD_REVENUE, iqd(50)).credit(
            merchant_payable(uuid4()), iqd(50)
        ).build(uuid4())


def test_a_posting_of_zero_is_refused() -> None:
    """Zero moves nothing; recording it would be a line with no meaning."""
    with pytest.raises(ZeroPosting):
        draft().debit(BANK, iqd(0))


def test_a_negative_amount_cannot_exist_at_all() -> None:
    """Direction is carried by the side, never by the sign."""
    with pytest.raises(NegativeAmount):
        iqd(-1)


def test_one_entry_one_currency() -> None:
    entry = draft()
    entry.postings.append(
        Posting(account=BANK, side=Side.DEBIT, amount=Money(100, Currency.IQD))
    )
    entry.postings.append(
        Posting(
            account=HUDHUD_REVENUE, side=Side.CREDIT, amount=Money(100, Currency.IQD)
        )
    )
    built = entry.build(uuid4())
    assert built.currency is Currency.IQD


def test_a_two_sided_split_balances() -> None:
    """One collection, two credits: the merchant's goods and HUDHUD's fee."""
    merchant = uuid4()
    entry = (
        draft()
        .debit(driver_custody(uuid4()), iqd(105_000))
        .credit(merchant_payable(merchant), iqd(100_000))
        .credit(HUDHUD_REVENUE, iqd(5_000))
        .build(uuid4())
    )
    assert entry.total == iqd(105_000)


# --------------------------------------------------------------- accounts


def test_a_party_scoped_account_must_name_its_party() -> None:
    """"Driver cash custody" is not a place; one driver's custody is."""
    with pytest.raises(ValueError, match="must name the party"):
        AccountRef(kind=AccountKind.DRIVER_CASH_CUSTODY)


def test_a_platform_account_names_no_party() -> None:
    with pytest.raises(ValueError, match="names no party"):
        AccountRef(kind=AccountKind.BANK, party_id=uuid4())


def test_asset_accounts_are_debit_normal() -> None:
    for account in (BANK, HUB_CASH, EXCHANGE_IN_TRANSIT, driver_custody(uuid4())):
        assert account.is_debit_normal is True


def test_liability_and_revenue_accounts_are_credit_normal() -> None:
    for account in (HUDHUD_REVENUE, merchant_payable(uuid4())):
        assert account.is_debit_normal is False


def test_the_account_kinds_are_exactly_the_ones_adr_0012_names() -> None:
    assert {kind.value for kind in AccountKind} == {
        "DRIVER_CASH_CUSTODY",
        "HUB_CASH",
        "EXCHANGE_IN_TRANSIT",
        "BANK",
        "MERCHANT_PAYABLE",
        "HUDHUD_REVENUE",
        "RECEIVER_REFUND_PAYABLE",
    }


# --------------------------------------------------------------- balances


def test_a_balance_is_the_sum_of_its_postings() -> None:
    driver = uuid4()
    custody = driver_custody(driver)
    entries = [
        draft().debit(custody, iqd(100_000)).credit(HUDHUD_REVENUE, iqd(100_000)).build(uuid4()),
        draft().debit(custody, iqd(50_000)).credit(HUDHUD_REVENUE, iqd(50_000)).build(uuid4()),
        draft().credit(custody, iqd(30_000)).debit(HUB_CASH, iqd(30_000)).build(uuid4()),
    ]
    assert balance_of(custody, entries).amount == iqd(120_000)


def test_a_credit_normal_balance_grows_on_a_credit() -> None:
    merchant = uuid4()
    payable = merchant_payable(merchant)
    entries = [
        draft().debit(BANK, iqd(80_000)).credit(payable, iqd(80_000)).build(uuid4())
    ]
    assert balance_of(payable, entries).amount == iqd(80_000)


def test_a_balance_that_went_negative_is_an_error_not_a_number() -> None:
    """A driver cannot hold minus cash. If the sum says so, a bad posting was made."""
    custody = driver_custody(uuid4())
    entries = [
        draft().credit(custody, iqd(10_000)).debit(HUB_CASH, iqd(10_000)).build(uuid4())
    ]
    with pytest.raises(LedgerError, match="went negative"):
        balance_of(custody, entries)


def test_an_untouched_account_has_a_zero_balance() -> None:
    assert balance_of(BANK, []).amount == iqd(0)


def test_a_balance_ignores_other_accounts() -> None:
    one = driver_custody(uuid4())
    other = driver_custody(uuid4())
    entries = [
        draft().debit(other, iqd(90_000)).credit(HUDHUD_REVENUE, iqd(90_000)).build(uuid4())
    ]
    assert balance_of(one, entries).amount == iqd(0)


# --------------------------------------------------------------- corrections


def test_a_mistake_is_corrected_by_a_reversing_entry() -> None:
    """ADR-0012 rollback: never a deletion and never an edit."""
    custody = driver_custody(uuid4())
    original = (
        draft().debit(custody, iqd(75_000)).credit(HUDHUD_REVENUE, iqd(75_000)).build(uuid4())
    )
    correction = reverse(
        original, entry_id=uuid4(), occurred_at=now(), actor_id=uuid4()
    )
    assert correction.reason is JournalReason.CORRECTION
    assert correction.corrects_entry_id == original.entry_id
    assert balance_of(custody, [original, correction]).amount == iqd(0)


def test_a_reversal_keeps_both_entries_on_the_record() -> None:
    custody = driver_custody(uuid4())
    original = (
        draft().debit(custody, iqd(1_000)).credit(HUDHUD_REVENUE, iqd(1_000)).build(uuid4())
    )
    correction = reverse(original, entry_id=uuid4(), occurred_at=now(), actor_id=None)
    assert balance_of(custody, [original, correction]).posting_count == 2


def test_a_reversal_of_a_reversal_restores_the_original() -> None:
    custody = driver_custody(uuid4())
    original = (
        draft().debit(custody, iqd(2_000)).credit(HUDHUD_REVENUE, iqd(2_000)).build(uuid4())
    )
    undo = reverse(original, entry_id=uuid4(), occurred_at=now(), actor_id=None)
    redo = reverse(undo, entry_id=uuid4(), occurred_at=now(), actor_id=None)
    assert balance_of(custody, [original, undo, redo]).amount == iqd(2_000)


# --------------------------------------------------------------- money


def test_money_is_never_a_float() -> None:
    with pytest.raises(TypeError):
        Money(minor_units=1.5)


def test_a_bool_is_not_an_amount() -> None:
    """``True`` is an ``int`` in Python; a ledger should not accept it as one dinar."""
    with pytest.raises(TypeError):
        Money(minor_units=True)


def _in_a_second_currency(amount: Money) -> Money:
    """Build money in a currency that does not exist yet.

    ``Currency`` has one member today, so the mixed-currency guards cannot be reached
    by ordinary construction. They are forward-looking — ADR-0012 keeps the currency
    explicit precisely "so a second currency cannot be introduced by accident" — and a
    guard that has never fired is a guard nobody has checked. Reaching around the frozen
    dataclass is the honest way to make it fire.
    """
    other = Money(minor_units=amount.minor_units)
    object.__setattr__(other, "currency", _SECOND_CURRENCY)
    return other


class _SecondCurrency(str):
    """Stands in for a currency HUDHUD does not trade in yet."""

    value = "USD"

    def __repr__(self) -> str:
        return "USD"


_SECOND_CURRENCY = _SecondCurrency("USD")


def test_money_of_different_currencies_cannot_be_added() -> None:
    with pytest.raises(CurrencyMismatch):
        iqd(10) + _in_a_second_currency(iqd(10))


def test_money_of_different_currencies_cannot_be_compared() -> None:
    with pytest.raises(CurrencyMismatch):
        _ = iqd(10) < _in_a_second_currency(iqd(20))


def test_a_mixed_currency_entry_is_refused() -> None:
    """One entry, one currency — otherwise its two sides are not comparable at all."""
    with pytest.raises(MixedCurrencyEntry):
        JournalEntry(
            entry_id=uuid4(),
            reason=JournalReason.COD_CASH_COLLECTED,
            postings=(
                Posting(account=BANK, side=Side.DEBIT, amount=iqd(10)),
                Posting(
                    account=HUDHUD_REVENUE,
                    side=Side.CREDIT,
                    amount=_in_a_second_currency(iqd(10)),
                ),
            ),
            occurred_at=now(),
        )


def test_a_large_amount_stays_exact() -> None:
    """No float: nine billion dinars is nine billion dinars."""
    huge = iqd(9_000_000_000)
    entry = draft().debit(BANK, huge).credit(HUDHUD_REVENUE, huge).build(uuid4())
    assert entry.total.minor_units == 9_000_000_000
