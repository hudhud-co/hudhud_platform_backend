"""Value objects for merchant status, stores, team, label stock and standing policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ApplicationStatus(StrEnum):
    """Lifecycle of a request for merchant status (v6.3 p.9, Figure 2).

    ``CHANGES_REQUESTED`` is the app's "Needs changes" decision, not a final rejection:
    v6.3 says a rejected applicant "stays a regular customer (may re-apply later)" and
    Customer App v3 ``storeRejected`` keeps the answers ("Your answers are kept") behind
    an "Edit and resubmit" action. ``APPROVED`` is terminal — a merchant is not re-reviewed
    through the application aggregate.
    """

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    WITHDRAWN = "WITHDRAWN"


#: Statuses that still occupy the applicant's single open application slot. The app is
#: explicit: "One application at a time." (Customer App v3 ``storeReview``.)
OPEN_APPLICATION_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {ApplicationStatus.DRAFT, ApplicationStatus.SUBMITTED, ApplicationStatus.CHANGES_REQUESTED}
)

#: Permitted application transitions. Everything absent here is refused.
APPLICATION_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: frozenset(
        {ApplicationStatus.SUBMITTED, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.SUBMITTED: frozenset(
        {
            ApplicationStatus.APPROVED,
            ApplicationStatus.CHANGES_REQUESTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    ApplicationStatus.CHANGES_REQUESTED: frozenset(
        {ApplicationStatus.SUBMITTED, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.APPROVED: frozenset(),
    ApplicationStatus.WITHDRAWN: frozenset(),
}


class MerchantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class DeliveryFeePayer(StrEnum):
    """Who pays the delivery fee under normal (non-refusal) circumstances.

    v6.3 p.13 lists this among the things *the sender sets*. Refusal is different and is
    not a sender choice: p.13 fixes the return-trip and delivery fee to the merchant
    "always ... regardless of reason".
    """

    SENDER = "SENDER"
    RECEIVER = "RECEIVER"


class TeamRole(StrEnum):
    """Store team roles.

    Customer App v3 ``teamForm`` states plainly: "More roles are coming. For now every
    member is a warehouse keeper." Modelling one role is the evidence-backed choice; a
    second role would be invented.
    """

    WAREHOUSE_KEEPER = "WAREHOUSE_KEEPER"


class MembershipStatus(StrEnum):
    """A store team membership.

    ``PENDING`` grants nothing. Customer App v3 ``teamSent``: "They have to accept it in
    their own account. Until then the member stays pending and sees nothing of your store."
    """

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DECLINED = "DECLINED"
    REMOVED = "REMOVED"


class StorePermission(StrEnum):
    """What a store team member may do.

    Derived verbatim from ``roleCanLine`` / ``roleCannotLine`` in Customer App v3: a
    warehouse keeper "Sees the store's parcels and their details, prepares them and
    completes the courier handover" and "Cannot create, edit or cancel a shipment, and
    never sees the wallet or COD figures."
    """

    STORE_PARCEL_READ = "store_parcel:read"
    STORE_PARCEL_PREPARE = "store_parcel:prepare"
    COURIER_HANDOVER_COMPLETE = "courier_handover:complete"


#: The fixed permission set per role. There is no per-member override: an override would
#: be the mechanism by which a warehouse keeper could be handed wallet access.
ROLE_PERMISSIONS: dict[TeamRole, frozenset[StorePermission]] = {
    TeamRole.WAREHOUSE_KEEPER: frozenset(
        {
            StorePermission.STORE_PARCEL_READ,
            StorePermission.STORE_PARCEL_PREPARE,
            StorePermission.COURIER_HANDOVER_COMPLETE,
        }
    ),
}

#: Capabilities no store team member may ever hold, whatever the role. Kept as an explicit
#: denylist so that adding a future role cannot silently widen a keeper's reach.
FORBIDDEN_TEAM_CAPABILITIES: frozenset[str] = frozenset(
    {
        "shipment:create",
        "shipment:edit",
        "shipment:cancel",
        "wallet:read",
        "cod:read",
        "payout:request",
    }
)


class StockKind(StrEnum):
    """What a batch of barcoded consumables is for.

    Both are stock a merchant holds before any parcel exists, both carry scannable codes,
    and both are counted down as they are used — but only labels may be self-printed
    (v6.3 p.12), and only seals are a purchased per-parcel add-on (p.14).
    """

    LABEL = "LABEL"
    PACKAGING_SEAL = "PACKAGING_SEAL"


class LabelStockSource(StrEnum):
    """Where a merchant's labels came from.

    v6.3 p.10: labels are not printed on demand — an approved merchant "already keeps a
    stock of pre-printed barcode labels on hand". p.12 resolves self-printing: permitted,
    "but only using a specific printer and blank label stock that Hudhud provides".
    """

    HUDHUD_PREPRINTED = "HUDHUD_PREPRINTED"
    MERCHANT_SELF_PRINTED = "MERCHANT_SELF_PRINTED"


class PrinterAuthorizationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """A map pin for a store location. Optional — v6.3 p.12 keeps precise location optional."""

    latitude: Decimal
    longitude: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("-90") <= self.latitude <= Decimal("90")):
            msg = "latitude must be between -90 and 90"
            raise ValueError(msg)
        if not (Decimal("-180") <= self.longitude <= Decimal("180")):
            msg = "longitude must be between -180 and 180"
            raise ValueError(msg)


#: Iraqi governorates. A store location must name one, for the same routing reason a
#: receiver must (v6.3 p.12).
GOVERNORATES: frozenset[str] = frozenset(
    {
        "BAGHDAD", "BASRA", "NINEVEH", "ERBIL", "SULAYMANIYAH", "DUHOK", "KIRKUK",
        "NAJAF", "KARBALA", "BABIL", "WASIT", "MAYSAN", "DHI_QAR", "MUTHANNA",
        "QADISIYYAH", "DIYALA", "ANBAR", "SALAH_AL_DIN", "HALABJA",
    }
)

_MERCHANT_CODE = re.compile(r"^[A-Z0-9]{4,16}$")


def normalize_governorate(raw: str) -> str:
    """Normalise a governorate to its canonical token, or raise ``ValueError``."""
    token = raw.strip().upper().replace(" ", "_").replace("-", "_")
    if token not in GOVERNORATES:
        msg = f"unknown governorate: {raw}"
        raise ValueError(msg)
    return token


def validate_merchant_code(code: str) -> str:
    """Merchant codes are quoted to support and printed on labels, so keep them strict."""
    candidate = code.strip().upper()
    if not _MERCHANT_CODE.match(candidate):
        msg = f"invalid merchant code: {code}"
        raise ValueError(msg)
    return candidate
