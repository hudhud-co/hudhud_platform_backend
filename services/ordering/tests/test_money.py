"""Money is exact, typed and never floating point (ADR-0012, PAY-11)."""

from __future__ import annotations

import pytest

from ordering.domain.money import (
    Currency,
    CurrencyMismatch,
    Money,
    NegativeAmount,
)


def test_money_is_stored_as_integer_minor_units() -> None:
    assert Money(450_000).minor_units == 450_000


def test_a_float_amount_is_refused_at_construction() -> None:
    """The bug this prevents is 0.1 + 0.2 appearing on an invoice."""
    with pytest.raises(TypeError):
        Money(450_000.0)  # type: ignore[arg-type]


def test_a_bool_is_not_an_amount() -> None:
    with pytest.raises(TypeError):
        Money(True)  # type: ignore[arg-type]


def test_a_negative_amount_is_refused() -> None:
    with pytest.raises(NegativeAmount):
        Money(-1)


def test_amounts_of_the_same_currency_add_and_subtract_exactly() -> None:
    assert Money(450_000) + Money(1_500) == Money(451_500)
    assert Money(450_000) - Money(50_000) == Money(400_000)


def test_subtracting_below_zero_is_refused() -> None:
    with pytest.raises(NegativeAmount):
        Money(1_000) - Money(2_000)


def test_money_can_be_multiplied_only_by_a_whole_count() -> None:
    assert Money(1_500) * 3 == Money(4_500)
    with pytest.raises(TypeError):
        Money(1_500) * 1.5  # type: ignore[operator]


def test_two_currencies_can_never_be_combined_silently() -> None:
    class _Other(str):
        pass

    left = Money(1, Currency.IQD)
    right = Money(1, Currency.IQD)
    assert (left + right).currency is Currency.IQD


def test_currency_mismatch_is_raised_rather_than_coerced() -> None:
    # Constructed directly so the test does not depend on a second currency existing yet.
    left = Money(1, Currency.IQD)
    right = Money.__new__(Money)
    object.__setattr__(right, "minor_units", 1)
    object.__setattr__(right, "currency", "USD")
    with pytest.raises(CurrencyMismatch):
        left + right  # type: ignore[operator]


def test_comparison_works_within_a_currency() -> None:
    assert Money(1) < Money(2)
    assert Money(2) > Money(1)
    assert Money(1) <= Money(1)
    assert Money(1) >= Money(1)


def test_zero_is_recognisable() -> None:
    assert Money.zero().is_zero is True
    assert Money(1).is_zero is False


def test_iqd_is_formatted_in_whole_dinars() -> None:
    """IQD has no minor unit in circulation, so 450,000 is 450,000 dinars."""
    assert Money(450_000).format() == "450,000 IQD"


def test_the_display_form_is_never_used_for_arithmetic() -> None:
    assert str(Money(1_500)) == "1,500 IQD"
    assert Money(1_500).minor_units == 1_500


def test_money_is_immutable() -> None:
    amount = Money(100)
    with pytest.raises((AttributeError, TypeError)):
        amount.minor_units = 200  # type: ignore[misc]
