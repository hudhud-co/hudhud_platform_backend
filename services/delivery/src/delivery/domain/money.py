"""Money for the Delivery context.

Every amount is an integer count of minor units plus an explicit currency (ADR-0012).
There is no float anywhere in this module and no implicit currency: a bare integer can be
added to the wrong kind of money silently, and a float cannot represent 450,000 IQD split
three ways without drifting.

IQD has no minor unit in circulation, so one minor unit is one dinar. Keeping the scale
explicit rather than assuming it means a second currency can be added without rereading
every call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Currency(StrEnum):
    IQD = "IQD"


#: Minor units per major unit, per currency. IQD is quoted and collected in whole dinars.
MINOR_UNITS: dict[Currency, int] = {Currency.IQD: 1}


class CurrencyMismatch(ValueError):
    def __init__(self, left: Currency, right: Currency) -> None:
        super().__init__(f"cannot combine {left} and {right}")


class NegativeAmount(ValueError):
    def __init__(self, amount: int) -> None:
        super().__init__(f"amount cannot be negative: {amount}")


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact amount. ``minor_units`` is always an ``int``."""

    minor_units: int
    currency: Currency = Currency.IQD

    def __post_init__(self) -> None:
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            msg = f"minor_units must be an int, got {type(self.minor_units).__name__}"
            raise TypeError(msg)
        if self.minor_units < 0:
            raise NegativeAmount(self.minor_units)

    @classmethod
    def zero(cls, currency: Currency = Currency.IQD) -> Money:
        return cls(0, currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise CurrencyMismatch(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __mul__(self, count: int) -> Money:
        if not isinstance(count, int) or isinstance(count, bool):
            msg = "money may only be multiplied by a whole count"
            raise TypeError(msg)
        return Money(self.minor_units * count, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units >= other.minor_units

    @property
    def is_zero(self) -> bool:
        return self.minor_units == 0

    def format(self) -> str:
        """Display form — 450,000 IQD. Never used for arithmetic."""
        scale = MINOR_UNITS[self.currency]
        if scale == 1:
            return f"{self.minor_units:,} {self.currency.value}"
        major, minor = divmod(self.minor_units, scale)
        width = len(str(scale)) - 1
        return f"{major:,}.{minor:0{width}d} {self.currency.value}"

    def __str__(self) -> str:
        return self.format()
