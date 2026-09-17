"""Domain errors for the Claims service.

``HighValueThresholdNotSet`` is the one v6.3 Open Item that lands here (CLM-08). It
refuses exactly one thing — deciding whether a claim counts as high value — and nothing
else about filing, reviewing, approving or rejecting depends on it.
"""

from __future__ import annotations


class ClaimsError(Exception):
    """Base class for everything this context refuses."""


# ------------------------------------------------------------------ not found


class ClaimNotFound(ClaimsError):
    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__(f"claim not found: {reference}")


class IncidentNotFound(ClaimsError):
    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__(f"incident not found: {reference}")


# ------------------------------------------------------------------ filing


class PhotographsRequired(ClaimsError):
    """Customer App v3 `photosRequired`, under "Damaged in transit"."""

    def __init__(self) -> None:
        super().__init__(
            "a damage claim needs at least one photograph; without one there is "
            "nothing to check the custody record against"
        )


class InvalidTrackingCode(ClaimsError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"not a tracking code: {tracking_code}")


class ClaimAlreadyOpenForThisParcel(ClaimsError):
    """One open claim per parcel — a second is the same conversation twice."""

    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__(f"this parcel already has an open claim: {reference}")


class NoReturnWindowAfterAcceptance(ClaimsError):
    """CLM-06 — v6.3: there is no return window after acceptance at the door.

    The message names the recourse that *does* remain. No return window is not the same
    as no recourse, and a receiver told only "no" will phone support to be told the
    same thing again.
    """

    def __init__(self) -> None:
        super().__init__(
            "acceptance at the door is final; v6.3 provides no return window "
            "afterwards. A compensation claim is still possible if the parcel was "
            "damaged, lost or short."
        )


class LiabilityEndedAtTheDoor(ClaimsError):
    """CLM-05, v6.3 p.37 — it ends when the receiver takes it inside to test it."""

    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        super().__init__(
            "HUDHUD's responsibility for this parcel ended at the door "
            f"({boundary}); v6.3 p.37 is explicit that testing it inside ends liability"
        )


# ------------------------------------------------------------------ review


class ClaimTransitionNotAllowed(ClaimsError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"a claim cannot go from {current} to {target}")


class CustodyRecordsNotReviewed(ClaimsError):
    """CLM-03 — "Hudhud reviews scan and custody records before compensating"."""

    def __init__(self) -> None:
        super().__init__(
            "the scan and custody records have not been reviewed; v6.3 p.42 makes that "
            "a step before compensating, not a formality"
        )


class RejectionReasonRequired(ClaimsError):
    """CLM-04 — "rejected ⇒ documented reason given"."""

    def __init__(self) -> None:
        super().__init__("a rejected claim must carry a documented reason")


class CompensationAmountRequired(ClaimsError):
    def __init__(self) -> None:
        super().__init__("an approved claim must say what is being compensated")


class OnlyOperationsDecidesAClaim(ClaimsError):
    def __init__(self) -> None:
        super().__init__("only operations or an accountant may decide a claim")


class OnlySupportOrOperationsReviews(ClaimsError):
    def __init__(self) -> None:
        super().__init__("only support or operations may review a claim")


class NotYourClaim(ClaimsError):
    """A claimant sees their own claim and no one else's."""

    def __init__(self) -> None:
        super().__init__("this claim belongs to someone else")


class ClaimAlreadyDecided(ClaimsError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"this claim is already {status}")


class MessageBodyRequired(ClaimsError):
    def __init__(self) -> None:
        super().__init__("a support message needs something in it")


class ClosedClaimTakesNoMessages(ClaimsError):
    def __init__(self) -> None:
        super().__init__("this claim is closed; reopen it or file a new one")


# ------------------------------------------------------------------ incidents


class IncidentTransitionNotAllowed(ClaimsError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"an incident cannot go from {current} to {target}")


class IncidentNeedsAParcel(ClaimsError):
    """DRV-P25 — every kind except a vehicle or safety issue is about one parcel."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"a {kind} report must name the parcel it is about")


class OnlyOperationsResolvesAnIncident(ClaimsError):
    """OPS-07 — the driver reports; operations resolves."""

    def __init__(self) -> None:
        super().__init__("only operations may resolve a driver incident")


class ResolutionNoteRequired(ClaimsError):
    def __init__(self) -> None:
        super().__init__("resolving an incident must say what was found")


class DriverMayNotSeeCompensation(ClaimsError):
    """SEC-07 — Driver App v8: "No compensation or claim value is shown to the driver".

    Raised where a caller asks for a driver-facing view *of* a value. The ordinary
    driver read models cannot carry one at all, so this exists to make a deliberate
    attempt fail loudly rather than return something empty and ambiguous.
    """

    def __init__(self) -> None:
        super().__init__(
            "a driver is never shown a compensation or claim value (v6.3 SEC-07)"
        )


# ------------------------------------------------- the v6.3 Open Item (CLM-08)


class HighValueThresholdNotSet(ClaimsError):
    """CLM-08 — the declared-value threshold above which extra handling applies.

    Deliberately a hard refusal rather than a default. A guessed threshold silently
    changes which parcels get extra scrutiny and what HUDHUD is exposed to, and it is a
    number an accountant and the product owner have to agree, not one this service can
    pick. Everything else about a claim — filing, evidence, review, approval, rejection,
    the support thread — works without it.
    """

    def __init__(self) -> None:
        super().__init__(
            "the high-value declared-value threshold is an unresolved v6.3 Open Item "
            "(Appendix A p.44); set CLAIMS_HIGH_VALUE_THRESHOLD once it is decided"
        )


# ------------------------------------------------------------------ concurrency


class StaleClaimsRecord(ClaimsError):
    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed underneath this write")
