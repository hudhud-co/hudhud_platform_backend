"""The delivery code, and the unresolved contradiction about how long it is.

## The conflict (DRV-L05)

Two authoritative product sources disagree, in as many words:

* **Driver App v8** collects **six** digits. `lmOtp` renders six boxes, the fixture code
  is `482913`, and the button is disabled with "Enter all 6 digits".
* **Customer App v3** tells the receiver it is **four**: `deliveryCodeNote` reads
  "Give this 4-digit code to the courier so the drop can be confirmed", and the Arabic
  says the same — "الرمز المكوّن من 4 أرقام".

Both are shipped product text describing the same code — the customer string sits directly
under `deliveryCode: 'Delivery code'` and `appearsWhenOut: 'Appears when out for
delivery'`. This is not an app-layer nicety: a receiver told to expect four digits cannot
satisfy a driver app demanding six, and the parcel does not get handed over.

## How this service behaves

The length is **configuration with no default**, and verification **fails closed** while it
is unset. Picking one silently would either lock out every receiver who was told four, or
build a weaker code than the driver app was designed for. Neither is ours to choose.

Set `DELIVERY_CODE_LENGTH` once the business resolves it and the whole flow opens with no
code change. Everything around the code — the ten-minute wait, the ID fallback, the seal,
the inspection, the payment, the outcomes — is fully implemented and tested meanwhile.

## What is *not* in doubt

v6.3 p.26 settles two things the apps agree on, and both are enforced here:

* **anyone holding the code may receive the parcel**, regardless of identity;
* the code is compared, never displayed, logged, or stored in the clear.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

#: The two lengths the sources give. Recorded so the disagreement is in the code, not
#: only in a document, and so a future decision can be checked against both.
CONFLICTING_LENGTHS: tuple[int, ...] = (4, 6)

CONFLICT_SOURCES: dict[int, str] = {
    4: "Customer App v3 `deliveryCodeNote`: 'Give this 4-digit code to the courier'",
    6: "Driver App v8 `lmOtp`: six input boxes, fixture `482913`, 'Enter all 6 digits'",
}


class DeliveryCodeLengthNotDecided(RuntimeError):
    """Raised when a code is checked before the business has settled its length.

    Deliberately a hard failure rather than a default: a guessed length is the difference
    between a parcel being handed over and a receiver being turned away.
    """

    def __init__(self) -> None:
        super().__init__(
            "the delivery-code length is an unresolved contradiction between the "
            "Driver App (6 digits) and the Customer App (4 digits); set "
            "DELIVERY_CODE_LENGTH to the decided value before codes can be verified"
        )


@dataclass(frozen=True, slots=True)
class DeliveryCodePolicy:
    """How long a delivery code is, and how many tries a driver gets.

    ``length`` of ``None`` is the shipped state and means the decision has not been made.
    """

    length: int | None = None
    max_attempts: int = 5

    @property
    def is_decided(self) -> bool:
        return self.length is not None

    def assert_decided(self) -> int:
        if self.length is None:
            raise DeliveryCodeLengthNotDecided()
        return self.length

    def matches_shape(self, candidate: str) -> bool:
        """Shape check only — never a comparison against the real code."""
        expected = self.assert_decided()
        return len(candidate) == expected and candidate.isdigit()


def hash_delivery_code(code: str, *, key: str, stop_reference: str) -> str:
    """Keyed digest of a delivery code, bound to the stop it belongs to.

    Binding the digest to the stop means a code captured from one parcel cannot be
    replayed against another, even though short numeric codes inevitably repeat across a
    network of this size.
    """
    material = f"{stop_reference}:{code}"
    return hmac.new(
        key.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_delivery_code(
    *, candidate: str, expected_digest: str, key: str, stop_reference: str
) -> bool:
    """Constant-time comparison. A timing difference here is a guessing oracle."""
    return hmac.compare_digest(
        hash_delivery_code(candidate, key=key, stop_reference=stop_reference),
        expected_digest,
    )
