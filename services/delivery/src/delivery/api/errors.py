"""Domain error to HTTP mapping for the Delivery service.

Two of these mappings carry a business decision rather than a convention.

``DeliveryCodeLengthNotDecided`` and ``IdPhotoRetentionNotDecided`` answer **501**, not
400 or 503: the request is well formed, the service is healthy, and the operation is
simply not implementable until the business decides. A 400 would blame the driver and a
503 would suggest retrying.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from delivery.domain.delivery_code import DeliveryCodeLengthNotDecided
from delivery.domain.errors import (
    BrokenSealStopsTheHandover,
    DeliveryCodeIncorrect,
    DeliveryCodeNotSet,
    DeliveryError,
    FailedAttemptNotFound,
    IdFallbackIsOnlyForTheNamedReceiver,
    InvalidHandoverWindow,
    InvalidPosReference,
    ManifestNotFound,
    NextAttemptAlreadyDecided,
    NoNamedReceiverOnThisParcel,
    NothingToCollect,
    NotThisDriversStop,
    NotVerified,
    OnlyOperationsDecidesTheNextAttempt,
    OpenBoxNotAllowed,
    ParcelAlreadyOnAManifest,
    PaymentAlreadyRecorded,
    PaymentRequiredBeforeHandover,
    PhotographyNotEnabled,
    PhotoRequiredAfterInspection,
    PhotoRequiredBeforeOpening,
    PosProofRequired,
    RatingAlreadyGiven,
    RatingNotYetPossible,
    RatingOutOfRange,
    SealMustBeCheckedFirst,
    StaleDeliveryRecord,
    StopAlreadyClosed,
    StopNotFound,
    StopTransitionNotAllowed,
    TooManyCodeAttempts,
    WaitNotOver,
    WaitNotStarted,
)
from delivery.domain.id_evidence import IdPhotoRetentionNotDecided

_STATUS: dict[type[Exception], int] = {
    ManifestNotFound: 404,
    StopNotFound: 404,
    FailedAttemptNotFound: 404,
    ParcelAlreadyOnAManifest: 409,
    StopTransitionNotAllowed: 409,
    StopAlreadyClosed: 409,
    # 403: the request is well formed; this driver is not the one carrying the parcel.
    NotThisDriversStop: 403,
    OnlyOperationsDecidesTheNextAttempt: 403,
    # v6.3 p.26 — the ten minutes are a rule, not a suggestion.
    WaitNotStarted: 409,
    WaitNotOver: 409,
    DeliveryCodeNotSet: 409,
    DeliveryCodeIncorrect: 422,
    # 429: the attempt limit, not a malformed request.
    TooManyCodeAttempts: 429,
    IdFallbackIsOnlyForTheNamedReceiver: 403,
    NoNamedReceiverOnThisParcel: 409,
    NotVerified: 409,
    SealMustBeCheckedFirst: 409,
    BrokenSealStopsTheHandover: 409,
    OpenBoxNotAllowed: 403,
    PhotoRequiredBeforeOpening: 409,
    PhotoRequiredAfterInspection: 409,
    PhotographyNotEnabled: 403,
    PaymentRequiredBeforeHandover: 409,
    PosProofRequired: 422,
    NothingToCollect: 409,
    PaymentAlreadyRecorded: 409,
    InvalidPosReference: 422,
    NextAttemptAlreadyDecided: 409,
    RatingOutOfRange: 422,
    RatingNotYetPossible: 409,
    RatingAlreadyGiven: 409,
    InvalidHandoverWindow: 422,
    StaleDeliveryRecord: 409,
    # The two undecided product questions. See this module's docstring.
    DeliveryCodeLengthNotDecided: 501,
    IdPhotoRetentionNotDecided: 501,
}

_CODE: dict[type[Exception], str] = {
    ManifestNotFound: "manifest_not_found",
    StopNotFound: "stop_not_found",
    FailedAttemptNotFound: "failed_attempt_not_found",
    ParcelAlreadyOnAManifest: "parcel_already_on_a_manifest",
    StopTransitionNotAllowed: "stop_transition_not_allowed",
    StopAlreadyClosed: "stop_already_closed",
    NotThisDriversStop: "not_this_drivers_stop",
    OnlyOperationsDecidesTheNextAttempt: "only_operations_decides_the_next_attempt",
    WaitNotStarted: "wait_not_started",
    WaitNotOver: "wait_not_over",
    DeliveryCodeNotSet: "delivery_code_not_set",
    DeliveryCodeIncorrect: "delivery_code_incorrect",
    TooManyCodeAttempts: "too_many_code_attempts",
    IdFallbackIsOnlyForTheNamedReceiver: "id_fallback_is_only_for_the_named_receiver",
    NoNamedReceiverOnThisParcel: "no_named_receiver_on_this_parcel",
    NotVerified: "not_verified",
    SealMustBeCheckedFirst: "seal_must_be_checked_first",
    BrokenSealStopsTheHandover: "broken_seal_stops_the_handover",
    OpenBoxNotAllowed: "open_box_not_allowed",
    PhotoRequiredBeforeOpening: "photo_required_before_opening",
    PhotoRequiredAfterInspection: "photo_required_after_inspection",
    PhotographyNotEnabled: "photography_not_enabled",
    PaymentRequiredBeforeHandover: "payment_required_before_handover",
    PosProofRequired: "pos_proof_required",
    NothingToCollect: "nothing_to_collect",
    PaymentAlreadyRecorded: "payment_already_recorded",
    InvalidPosReference: "invalid_pos_reference",
    NextAttemptAlreadyDecided: "next_attempt_already_decided",
    RatingOutOfRange: "rating_out_of_range",
    RatingNotYetPossible: "rating_not_yet_possible",
    RatingAlreadyGiven: "rating_already_given",
    InvalidHandoverWindow: "invalid_handover_window",
    StaleDeliveryRecord: "stale_delivery_record",
    DeliveryCodeLengthNotDecided: "delivery_code_length_not_decided",
    IdPhotoRetentionNotDecided: "id_photo_retention_not_decided",
}


def raise_http_for_domain_error(error: Exception) -> NoReturn:
    """Translate a domain error into its HTTP answer.

    The detail carries a stable machine code and nothing else from the exception: a
    message could contain a tracking code or a receiver's name, and this is the one
    place where a careless f-string would put it in a client response and a log line.
    """
    status = _STATUS.get(type(error))
    if status is None:
        if isinstance(error, DeliveryError):
            status = 409
        else:
            raise error
    code = _CODE.get(type(error), "delivery_error")
    raise HTTPException(status_code=status, detail={"code": code})
