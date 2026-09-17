"""Value objects for customer profile, legal acceptance and the address book."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ProfileCompletionState(StrEnum):
    """How far a customer has got through first-run profile completion.

    The app asks for a name after the code is verified and before the home screen
    (Customer App v3 ``authOtp`` → ``authName`` → ``authTerms`` → ``authNotify``), so a
    signed-in principal with no profile is a normal state, not an error.
    """

    INCOMPLETE = "INCOMPLETE"
    COMPLETE = "COMPLETE"


class LegalDocumentKind(StrEnum):
    TERMS_OF_SERVICE = "TERMS_OF_SERVICE"
    PRIVACY_POLICY = "PRIVACY_POLICY"


class NotificationChannel(StrEnum):
    APP = "APP"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"


class AddressKind(StrEnum):
    """Whose address this is.

    A pickup address belongs to a store and is used when a courier collects; a delivery
    address belongs to a receiver. Keeping them distinct stops a receiver's home address
    from silently becoming a merchant pickup point.
    """

    DELIVERY = "DELIVERY"
    PICKUP = "PICKUP"


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """A map pin. Optional everywhere — v6.3 p.12 makes precise location optional."""

    latitude: Decimal
    longitude: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("-90") <= self.latitude <= Decimal("90")):
            msg = "latitude must be between -90 and 90"
            raise ValueError(msg)
        if not (Decimal("-180") <= self.longitude <= Decimal("180")):
            msg = "longitude must be between -180 and 180"
            raise ValueError(msg)


#: Iraqi governorates. The receiver's governorate is one of only two mandatory fields
#: v6.3 requires (p.12), because routing cannot happen without it.
GOVERNORATES: frozenset[str] = frozenset(
    {
        "BAGHDAD",
        "BASRA",
        "NINEVEH",
        "ERBIL",
        "SULAYMANIYAH",
        "DUHOK",
        "KIRKUK",
        "NAJAF",
        "KARBALA",
        "BABIL",
        "WASIT",
        "MAYSAN",
        "DHI_QAR",
        "MUTHANNA",
        "QADISIYYAH",
        "DIYALA",
        "ANBAR",
        "SALAH_AL_DIN",
        "HALABJA",
    }
)
