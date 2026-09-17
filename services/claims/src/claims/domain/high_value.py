"""CLM-08 — the high-value declared-value threshold, which nobody has set.

v6.3 Appendix A p.44 lists it as an Open Item. Above the threshold a parcel attracts
different handling and a different exposure for HUDHUD; below it, ordinary handling. The
number decides which parcels get extra scrutiny, so guessing it would quietly change what
HUDHUD is on the hook for — and it is a figure an accountant and the product owner have
to agree, not one this service can pick.

So the policy ships **with no default** and **fails closed**: asking whether a parcel is
high value raises. Everything else about a claim — filing it, attaching evidence,
reviewing the custody record, approving, rejecting, the support thread — works without
ever asking.
"""

from __future__ import annotations

from dataclasses import dataclass

from claims.domain.errors import HighValueThresholdNotSet
from claims.domain.money import Money


@dataclass(frozen=True, slots=True)
class HighValuePolicy:
    """``threshold`` of ``None`` is the shipped state: the decision has not been made."""

    threshold: Money | None = None

    @property
    def is_decided(self) -> bool:
        return self.threshold is not None

    def assert_decided(self) -> Money:
        if self.threshold is None:
            raise HighValueThresholdNotSet()
        return self.threshold

    def is_high_value(self, declared_value: Money) -> bool:
        """Refuses rather than guessing. The comparison is trivial; the number is not."""
        threshold = self.assert_decided()
        return declared_value >= threshold
