"""Domain errors for the Hub service."""

from __future__ import annotations


class HubError(Exception):
    """Base Hub domain error."""


# ------------------------------------------------------------------ lookup


class HubNotFound(HubError):
    def __init__(self, hub_id: str) -> None:
        self.hub_id = hub_id
        super().__init__(f"hub not found: {hub_id}")


class HubNotActive(HubError):
    def __init__(self, hub_id: str) -> None:
        self.hub_id = hub_id
        super().__init__(f"hub is not active: {hub_id}")


class DropOffNotFound(HubError):
    def __init__(self, drop_off_id: str) -> None:
        self.drop_off_id = drop_off_id
        super().__init__(f"drop-off not found: {drop_off_id}")


class ParcelNotInThisHub(HubError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"parcel is not in this hub: {tracking_code}")


class ConsignmentNotFound(HubError):
    def __init__(self, consignment_id: str) -> None:
        self.consignment_id = consignment_id
        super().__init__(f"consignment not found: {consignment_id}")


class LinehaulNotFound(HubError):
    def __init__(self, linehaul_id: str) -> None:
        self.linehaul_id = linehaul_id
        super().__init__(f"linehaul not found: {linehaul_id}")


# ------------------------------------------------------------------ drop-off


class DropOffTransitionNotAllowed(HubError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} drop-off to {target}")


class CustomerMayNotLabelTheirOwnParcel(HubError):
    """v6.3 p.18, CHANGED IN V6.3 (CUS-05).

    "Hub staff — not the customer — stick a label onto the parcel and scan it." The label
    is what links the box to the record, so who applies it is a control, not a courtesy.
    """

    def __init__(self) -> None:
        super().__init__(
            "hub staff — not the customer — stick the label on the parcel and scan it "
            "(v6.3 p.18)"
        )


class DropOffDetailsRequired(HubError):
    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__("drop-off details incomplete: " + ", ".join(missing))


class DropOffNotLabelled(HubError):
    """Custody cannot begin on a parcel that is not yet linked to a record."""

    def __init__(self) -> None:
        super().__init__(
            "a drop-off is accepted only once hub staff have scanned a label onto it"
        )


class DropOffExpired(HubError):
    """CUS-06 — an unclaimed drop-off order is cancelled after three days."""

    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"this drop-off lapsed and was cancelled: {tracking_code}")


class WeightRequiredAtDropOff(HubError):
    """CUS-07 — the hub weighs the parcel and prints the label at drop-off."""

    def __init__(self) -> None:
        super().__init__("the hub weighs the parcel before printing its label")


class CashOnDeliveryNotAvailableAtDropOff(HubError):
    """v6.3 p.19, Figure 6 — "no COD is available for this parcel"."""

    def __init__(self) -> None:
        super().__init__(
            "a regular customer's drop-off never carries cash on delivery (v6.3 p.8)"
        )


# ------------------------------------------------------------------ processing


class ParcelAlreadyReceived(HubError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"parcel already scanned in at this hub: {tracking_code}")


class ParcelNotSorted(HubError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"parcel has not been sorted yet: {tracking_code}")


class ParcelIsHeld(HubError):
    def __init__(self, tracking_code: str, reason: str) -> None:
        self.tracking_code = tracking_code
        self.reason = reason
        super().__init__(f"parcel {tracking_code} is held: {reason}")


class SameCityParcelDoesNotTravelBetweenHubs(HubError):
    """v6.3 p.22 — the hub-to-hub stage is skipped entirely for a same-city parcel."""

    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(
            f"{tracking_code} is travelling within one city, so it skips the "
            "hub-to-hub stage entirely"
        )


# ------------------------------------------------------------------ consignment


class ConsignmentTransitionNotAllowed(HubError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} consignment to {target}")


class ConsignmentIsEmpty(HubError):
    def __init__(self) -> None:
        super().__init__("a consignment must hold at least one parcel to be dispatched")


class ConsignmentAlreadySealed(HubError):
    def __init__(self, seal_code: str) -> None:
        self.seal_code = seal_code
        super().__init__(f"consignment is already sealed: {seal_code}")


class SealCodeInvalid(HubError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"not a usable seal code: {raw}")


class UnsealedConsignmentHasNothingToCheck(HubError):
    def __init__(self) -> None:
        super().__init__("this consignment was not sealed, so there is no seal to check")


class SealMismatchRequiresInvestigation(HubError):
    """v6.3 p.25 — a mismatch is never a silent pass-through.

    Raised when something tries to reconcile a consignment whose seal did not match.
    """

    def __init__(self, consignment_id: str) -> None:
        self.consignment_id = consignment_id
        super().__init__(
            f"consignment {consignment_id} failed its seal check and is under tamper "
            "investigation; it cannot be reconciled as if nothing happened"
        )


# ------------------------------------------------------------------ linehaul


class CutOffNotReached(HubError):
    """v6.3 p.23 — inter-city parcels move overnight, after that hub's own cut-off."""

    def __init__(self, hub_code: str, cut_off: str) -> None:
        self.hub_code = hub_code
        self.cut_off = cut_off
        super().__init__(
            f"{hub_code} dispatches after its own cut-off of {cut_off}; that time has "
            "not been reached"
        )


class LinehaulTransitionNotAllowed(HubError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} linehaul to {target}")


class LinehaulDriverMayNotSplitABatch(HubError):
    """v6.3 p.24 Role boundary — Linehaul Driver.

    "They do not have authority to split a grouped parcel batch early or deliver directly
    to a receiver."
    """

    def __init__(self) -> None:
        super().__init__(
            "a linehaul driver follows the route and reports deviation; they cannot "
            "split a grouped batch early or deliver directly to a receiver (v6.3 p.24)"
        )


# ------------------------------------------------------------------ misc


class UnknownGovernorate(HubError):
    def __init__(self, governorate: str) -> None:
        self.governorate = governorate
        super().__init__(f"unknown governorate: {governorate}")


class InvalidLabelCode(HubError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"not a usable label code: {raw}")


class StaleHubRecord(HubError):
    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed concurrently")
