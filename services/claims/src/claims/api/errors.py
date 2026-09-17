"""Domain error to HTTP mapping for the Claims service.

Two of these mappings carry a business decision rather than a convention.

``NoReturnWindowAfterAcceptance`` answers **409**, not 404: CLM-06 says the decision at
the door is final, and a 404 would suggest the route was merely missing and might appear
one day. ``HighValueThresholdNotSet`` answers **501**: the request is well formed and the
service is healthy, and the question is simply not answerable until the business decides.
A 400 would blame the caller and a 503 would suggest retrying.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from claims.domain.errors import (
    ClaimAlreadyDecided,
    ClaimAlreadyOpenForThisParcel,
    ClaimNotFound,
    ClaimsError,
    ClaimTransitionNotAllowed,
    ClosedClaimTakesNoMessages,
    CompensationAmountRequired,
    CustodyRecordsNotReviewed,
    DriverMayNotSeeCompensation,
    HighValueThresholdNotSet,
    IncidentNeedsAParcel,
    IncidentNotFound,
    IncidentTransitionNotAllowed,
    InvalidTrackingCode,
    LiabilityEndedAtTheDoor,
    MessageBodyRequired,
    NoReturnWindowAfterAcceptance,
    NotYourClaim,
    OnlyOperationsDecidesAClaim,
    OnlyOperationsResolvesAnIncident,
    OnlySupportOrOperationsReviews,
    PhotographsRequired,
    RejectionReasonRequired,
    ResolutionNoteRequired,
    StaleClaimsRecord,
)

_STATUS: dict[type[Exception], int] = {
    ClaimNotFound: 404,
    IncidentNotFound: 404,
    # 404, not 403: "their own claim" is the only claim that exists as far as a
    # claimant is concerned, and a 403 would confirm that a reference is real.
    NotYourClaim: 404,
    ClaimAlreadyOpenForThisParcel: 409,
    ClaimTransitionNotAllowed: 409,
    ClaimAlreadyDecided: 409,
    ClosedClaimTakesNoMessages: 409,
    IncidentTransitionNotAllowed: 409,
    CustodyRecordsNotReviewed: 409,
    # v6.3 p.37 — the receiver took it inside to test it. Well formed, and refused.
    LiabilityEndedAtTheDoor: 409,
    # CLM-06 — the decision at the door is final. See this module's docstring.
    NoReturnWindowAfterAcceptance: 409,
    PhotographsRequired: 422,
    InvalidTrackingCode: 422,
    IncidentNeedsAParcel: 422,
    MessageBodyRequired: 422,
    RejectionReasonRequired: 422,
    ResolutionNoteRequired: 422,
    CompensationAmountRequired: 422,
    OnlySupportOrOperationsReviews: 403,
    OnlyOperationsDecidesAClaim: 403,
    OnlyOperationsResolvesAnIncident: 403,
    # SEC-07 — a deliberate attempt to read a value as a driver.
    DriverMayNotSeeCompensation: 403,
    StaleClaimsRecord: 409,
    # The undecided product question. See this module's docstring.
    HighValueThresholdNotSet: 501,
}

_CODE: dict[type[Exception], str] = {
    ClaimNotFound: "claim_not_found",
    IncidentNotFound: "incident_not_found",
    NotYourClaim: "claim_not_found",
    ClaimAlreadyOpenForThisParcel: "claim_already_open_for_this_parcel",
    ClaimTransitionNotAllowed: "claim_transition_not_allowed",
    ClaimAlreadyDecided: "claim_already_decided",
    ClosedClaimTakesNoMessages: "closed_claim_takes_no_messages",
    IncidentTransitionNotAllowed: "incident_transition_not_allowed",
    CustodyRecordsNotReviewed: "custody_records_not_reviewed",
    LiabilityEndedAtTheDoor: "liability_ended_at_the_door",
    NoReturnWindowAfterAcceptance: "no_return_window_after_acceptance",
    PhotographsRequired: "photographs_required",
    InvalidTrackingCode: "invalid_tracking_code",
    IncidentNeedsAParcel: "incident_needs_a_parcel",
    MessageBodyRequired: "message_body_required",
    RejectionReasonRequired: "rejection_reason_required",
    ResolutionNoteRequired: "resolution_note_required",
    CompensationAmountRequired: "compensation_amount_required",
    OnlySupportOrOperationsReviews: "only_support_or_operations_reviews",
    OnlyOperationsDecidesAClaim: "only_operations_decides_a_claim",
    OnlyOperationsResolvesAnIncident: "only_operations_resolves_an_incident",
    DriverMayNotSeeCompensation: "driver_may_not_see_compensation",
    StaleClaimsRecord: "stale_claims_record",
    HighValueThresholdNotSet: "high_value_threshold_not_set",
}

#: Errors whose message is safe to return, because it explains a business decision the
#: caller needs to act on and carries no parcel, person or amount. Everything else
#: returns a code alone.
_EXPLAINED: frozenset[type[Exception]] = frozenset(
    {NoReturnWindowAfterAcceptance, HighValueThresholdNotSet, LiabilityEndedAtTheDoor}
)


def raise_http_for_domain_error(error: Exception) -> NoReturn:
    """Translate a domain error into its HTTP answer.

    The detail carries a stable machine code and, for the three decisions above, the
    sentence explaining them. Nothing else from the exception travels: a message could
    contain a tracking code or a claimant's words, and this is the one place where a
    careless f-string would put it into both a client response and a log line.
    """
    status = _STATUS.get(type(error))
    if status is None:
        if isinstance(error, ClaimsError):
            status = 409
        else:
            raise error
    detail: dict[str, str] = {"code": _CODE.get(type(error), "claims_error")}
    if type(error) in _EXPLAINED:
        detail["reason"] = str(error)
    raise HTTPException(status_code=status, detail=detail)
