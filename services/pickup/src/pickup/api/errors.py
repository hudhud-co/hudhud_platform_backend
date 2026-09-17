"""Stable HTTP mapping for Pickup recovery domain errors."""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import HTTPException

from pickup.domain.errors import (
    AssignmentAlreadyResolved,
    AssignmentNotAcknowledged,
    ConflictingIdempotencyKey,
    CourierChallengeAlreadyUsed,
    CourierChallengeExpired,
    CourierChallengeInvalidated,
    CourierChallengeIssueBlocked,
    CourierChallengeNotFound,
    CourierManifestMismatch,
    CourierManifestNotSubmitted,
    CourierManifestRequired,
    CourierSecretInvalid,
    CourierVerificationRateLimited,
    CourierVerificationRequired,
    CustodyAlreadyStarted,
    DriverNotAvailableForAssignment,
    DriverWorkSessionAlreadyOpen,
    DriverWorkSessionBlocked,
    DriverWorkSessionNotFound,
    DriverWorkSessionNotOpen,
    ExceptionEvidenceInsufficient,
    HandoverManifestEmpty,
    HandoverManifestItemNotFound,
    HandoverManifestNotArrived,
    HandoverManifestNotFound,
    HandoverManifestNotMutable,
    HubScopeNotAuthorized,
    InvalidRescheduleInput,
    InvalidTaskTransition,
    InvalidWorkSessionReason,
    MissingReassignmentDriver,
    OfflineAuthorizationInvalid,
    OfflineAuthorizationNotFound,
    OfflineAuthorizationRevoked,
    OfflineBatchTooLarge,
    OfflineOperationNotAuthorized,
    OfflineSyncDeadlinePassed,
    PackagingDecisionNotPermitted,
    PickupBatchNotFound,
    PickupError,
    PickupPhotoDocumentationMissing,
    PickupTaskAlreadyAccepted,
    PickupTaskNotAcceptable,
    PickupTaskNotFound,
    PickupTaskNotRecoverable,
    ReconciliationCaseAlreadyResolved,
    ReconciliationCaseNotFound,
    ScanIdentifierMissing,
    ScannedIdentifierMismatch,
    SenderMayNotVerifyOwnCourier,
    ShipmentAlreadyOnActiveManifest,
    ShipmentNotInDriverCustody,
    SigningKeyUnavailable,
    StalePickupTaskVersion,
    StopOutcomeAlreadyRecorded,
    StopOutcomeNotAllowed,
)
from pickup.domain.sanitize import sanitize_error_message
from pickup.ports.recovery_authorizer import AuthorizerUnavailableError

logger = logging.getLogger("pickup.api")

_ERROR_STATUS: dict[type[PickupError], int] = {
    PickupTaskNotFound: 404,
    PickupTaskAlreadyAccepted: 409,
    CustodyAlreadyStarted: 409,
    PickupTaskNotRecoverable: 409,
    ConflictingIdempotencyKey: 409,
    StalePickupTaskVersion: 409,
    InvalidRescheduleInput: 422,
    MissingReassignmentDriver: 422,
    # Driver workforce
    DriverWorkSessionNotFound: 404,
    DriverWorkSessionAlreadyOpen: 409,
    DriverWorkSessionNotOpen: 409,
    DriverWorkSessionBlocked: 409,
    DriverNotAvailableForAssignment: 409,
    InvalidWorkSessionReason: 422,
    # Driver task lifecycle
    AssignmentAlreadyResolved: 409,
    AssignmentNotAcknowledged: 409,
    InvalidTaskTransition: 409,
    PickupTaskNotAcceptable: 409,
    ScannedIdentifierMismatch: 422,
    ExceptionEvidenceInsufficient: 422,
    # Merchant stop outcomes and packaging decision
    PackagingDecisionNotPermitted: 409,
    PickupPhotoDocumentationMissing: 409,
    StopOutcomeAlreadyRecorded: 409,
    StopOutcomeNotAllowed: 409,
    PickupBatchNotFound: 404,
    ScanIdentifierMissing: 422,
    # Sender handover ceremony
    CourierChallengeNotFound: 404,
    CourierChallengeIssueBlocked: 409,
    CourierChallengeExpired: 409,
    CourierChallengeInvalidated: 409,
    CourierChallengeAlreadyUsed: 409,
    CourierSecretInvalid: 409,
    CourierVerificationRateLimited: 429,
    CourierVerificationRequired: 409,
    CourierManifestRequired: 409,
    CourierManifestMismatch: 409,
    CourierManifestNotSubmitted: 409,
    SenderMayNotVerifyOwnCourier: 403,
    # Hub handover
    HandoverManifestNotFound: 404,
    HandoverManifestItemNotFound: 404,
    HandoverManifestNotMutable: 409,
    HandoverManifestNotArrived: 409,
    HandoverManifestEmpty: 422,
    ShipmentNotInDriverCustody: 409,
    ShipmentAlreadyOnActiveManifest: 409,
    HubScopeNotAuthorized: 403,
    # Offline work
    OfflineAuthorizationNotFound: 404,
    OfflineAuthorizationRevoked: 403,
    OfflineAuthorizationInvalid: 401,
    OfflineSyncDeadlinePassed: 409,
    OfflineBatchTooLarge: 422,
    OfflineOperationNotAuthorized: 403,
    ReconciliationCaseNotFound: 404,
    ReconciliationCaseAlreadyResolved: 409,
    SigningKeyUnavailable: 503,
}

_ERROR_CODE: dict[type[PickupError], str] = {
    PickupTaskNotFound: "pickup_task_not_found",
    PickupTaskAlreadyAccepted: "pickup_task_already_accepted",
    CustodyAlreadyStarted: "custody_already_started",
    PickupTaskNotRecoverable: "pickup_task_not_recoverable",
    ConflictingIdempotencyKey: "conflicting_idempotency_key",
    StalePickupTaskVersion: "stale_pickup_task_version",
    InvalidRescheduleInput: "invalid_reschedule_input",
    MissingReassignmentDriver: "missing_reassignment_driver",
    DriverWorkSessionNotFound: "driver_work_session_not_found",
    DriverWorkSessionAlreadyOpen: "driver_work_session_already_open",
    DriverWorkSessionNotOpen: "driver_work_session_not_open",
    DriverWorkSessionBlocked: "driver_work_session_blocked",
    DriverNotAvailableForAssignment: "driver_not_available_for_assignment",
    InvalidWorkSessionReason: "invalid_work_session_reason",
    AssignmentAlreadyResolved: "assignment_already_resolved",
    AssignmentNotAcknowledged: "assignment_not_acknowledged",
    InvalidTaskTransition: "invalid_task_transition",
    PickupTaskNotAcceptable: "pickup_task_not_acceptable",
    ScannedIdentifierMismatch: "scanned_identifier_mismatch",
    ExceptionEvidenceInsufficient: "exception_evidence_insufficient",
    PackagingDecisionNotPermitted: "pickup_packaging_decision_not_permitted",
    PickupPhotoDocumentationMissing: "pickup_photo_documentation_missing",
    StopOutcomeAlreadyRecorded: "pickup_stop_outcome_already_recorded",
    StopOutcomeNotAllowed: "pickup_stop_outcome_not_allowed",
    PickupBatchNotFound: "pickup_batch_not_found",
    ScanIdentifierMissing: "scan_identifier_missing",
    CourierChallengeNotFound: "courier_challenge_not_found",
    CourierChallengeIssueBlocked: "courier_challenge_issue_blocked",
    CourierChallengeExpired: "courier_challenge_expired",
    CourierChallengeInvalidated: "courier_challenge_invalidated",
    CourierChallengeAlreadyUsed: "courier_challenge_already_used",
    CourierSecretInvalid: "courier_secret_invalid",
    CourierVerificationRateLimited: "courier_verification_rate_limited",
    CourierVerificationRequired: "courier_verification_required",
    CourierManifestRequired: "courier_manifest_required",
    CourierManifestMismatch: "courier_manifest_mismatch",
    CourierManifestNotSubmitted: "courier_manifest_not_submitted",
    SenderMayNotVerifyOwnCourier: "sender_may_not_verify_own_courier",
    HandoverManifestNotFound: "handover_manifest_not_found",
    HandoverManifestItemNotFound: "handover_manifest_item_not_found",
    HandoverManifestNotMutable: "handover_manifest_not_mutable",
    HandoverManifestNotArrived: "handover_manifest_not_arrived",
    HandoverManifestEmpty: "handover_manifest_empty",
    ShipmentNotInDriverCustody: "shipment_not_in_driver_custody",
    ShipmentAlreadyOnActiveManifest: "shipment_already_on_active_manifest",
    HubScopeNotAuthorized: "hub_scope_not_authorized",
    OfflineAuthorizationNotFound: "offline_authorization_not_found",
    OfflineAuthorizationRevoked: "offline_authorization_revoked",
    OfflineAuthorizationInvalid: "offline_authorization_invalid",
    OfflineSyncDeadlinePassed: "offline_sync_deadline_passed",
    OfflineBatchTooLarge: "offline_batch_too_large",
    OfflineOperationNotAuthorized: "offline_operation_not_authorized",
    ReconciliationCaseNotFound: "reconciliation_case_not_found",
    ReconciliationCaseAlreadyResolved: "reconciliation_case_already_resolved",
    SigningKeyUnavailable: "signing_key_unavailable",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    """Map domain/auth failures to HTTPException; never re-raise raw secrets."""
    if isinstance(exc, AuthorizerUnavailableError):
        raise HTTPException(status_code=503, detail="authorization unavailable") from None

    if isinstance(exc, PickupError):
        status = _ERROR_STATUS.get(type(exc), 400)
        code = _ERROR_CODE.get(type(exc), "pickup_error")
        detail: dict[str, object] = {
            "code": code,
            "message": sanitize_error_message(str(exc)),
        }
        blockers = getattr(exc, "blockers", None)
        if blockers:
            detail["blockers"] = list(blockers)
        logger.info("pickup_command_rejected code=%s status=%s", code, status)
        raise HTTPException(status_code=status, detail=detail) from None

    logger.exception("pickup_command_unexpected error=%s", sanitize_error_message(str(exc)))
    raise HTTPException(status_code=500, detail="internal error") from None
