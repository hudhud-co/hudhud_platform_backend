"""Domain errors for Pickup recovery and acceptance lifecycle."""

from __future__ import annotations


class PickupError(Exception):
    """Base Pickup domain error."""


class PickupTaskNotFound(PickupError):
    """Requested pickup task does not exist."""

    def __init__(self, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task not found: {pickup_task_id}")


class PickupTaskAlreadyAccepted(PickupError):
    """Accepted pickup tasks cannot enter recovery or be accepted again."""

    def __init__(self, *, pickup_task_id: str, acceptance_state: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.acceptance_state = acceptance_state
        super().__init__(
            f"pickup task {pickup_task_id} already accepted (state={acceptance_state})"
        )


class CustodyAlreadyStarted(PickupError):
    """Recovery is blocked when Shipment custody type is PICKUP_DRIVER."""

    def __init__(self, *, shipment_id: str, shipment_status: str) -> None:
        self.shipment_id = shipment_id
        self.shipment_status = shipment_status
        super().__init__(
            f"shipment {shipment_id} is in PICKUP_DRIVER custody (status={shipment_status})"
        )


class PickupTaskNotRecoverable(PickupError):
    """Task is terminal (superseded or cancelled) and cannot be recovered again."""

    def __init__(self, *, pickup_task_id: str, status: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.status = status
        super().__init__(f"pickup task {pickup_task_id} not recoverable (status={status})")


class PickupTaskNotAcceptable(PickupError):
    """Task is cancelled or superseded and cannot be accepted."""

    def __init__(self, *, pickup_task_id: str, status: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.status = status
        super().__init__(f"pickup task {pickup_task_id} not acceptable (status={status})")


class PickupTaskNotProofCaptured(PickupError):
    """Acceptance requires PROOF_CAPTURED status."""

    def __init__(self, *, pickup_task_id: str, current_status: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.current_status = current_status
        super().__init__(
            f"pickup task {pickup_task_id} must be PROOF_CAPTURED (status={current_status})"
        )


class PickupTaskMissingAssignedDriver(PickupError):
    """Acceptance requires an assigned driver."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task {pickup_task_id} missing assigned driver")


class PickupTaskMissingAssignedBatch(PickupError):
    """Acceptance requires an assigned batch."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task {pickup_task_id} missing assigned batch")


class ActingDriverMismatch(PickupError):
    """Acting driver must equal the assigned driver."""

    def __init__(self, *, pickup_task_id: str, acting_driver_user_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.acting_driver_user_id = acting_driver_user_id
        super().__init__(
            f"acting driver {acting_driver_user_id} is not assigned to pickup task "
            f"{pickup_task_id}"
        )


class PickupConditionProofMissing(PickupError):
    """Acceptance requires a Pickup condition-proof reference."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task {pickup_task_id} missing condition-proof reference")


class ExceptionEvidenceRequired(PickupError):
    """ACCEPTED_WITH_EXCEPTION requires at least one external media reference."""

    def __init__(self) -> None:
        super().__init__("ACCEPTED_WITH_EXCEPTION requires at least one media_refs entry")


class AcceptanceOutcomeNotAllowed(PickupError):
    """Only custody-starting outcomes may produce pickup.fact.accepted."""

    def __init__(self, *, outcome: str) -> None:
        self.outcome = outcome
        super().__init__(f"acceptance outcome not allowed for fact emission: {outcome}")


class InvalidRescheduleInput(PickupError):
    """Reschedule requires a valid scheduled window."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class MissingReassignmentDriver(PickupError):
    """Reassign recovery requires a new driver identifier."""

    def __init__(self) -> None:
        super().__init__("reassign recovery requires new_driver_user_id")


class ConflictingIdempotencyKey(PickupError):
    """The same idempotency key was reused with a different command."""

    def __init__(self, *, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"conflicting command for idempotency key: {idempotency_key}")


class StalePickupTaskVersion(PickupError):
    """Optimistic version mismatch — task changed since load."""

    def __init__(self, *, pickup_task_id: str, expected_version: int, actual_version: int) -> None:
        self.pickup_task_id = pickup_task_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"stale pickup task {pickup_task_id}: expected version {expected_version}, "
            f"got {actual_version}"
        )


class ContractAssetMissing(PickupError):
    """Registered contract assets are missing — fail closed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class EnvelopeContractValidationFailed(PickupError):
    """Generated envelope failed contract schema validation — fail closed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class DriverWorkSessionNotFound(PickupError):
    """No matching driver work session for this driver."""

    def __init__(self, *, driver_user_id: str) -> None:
        self.driver_user_id = driver_user_id
        super().__init__(f"no driver work session for driver {driver_user_id}")


class DriverWorkSessionAlreadyOpen(PickupError):
    """A driver may hold only one open pickup work session."""

    def __init__(self, *, driver_user_id: str, session_id: str, status: str) -> None:
        self.driver_user_id = driver_user_id
        self.session_id = session_id
        self.status = status
        super().__init__(
            f"driver {driver_user_id} already has an open work session "
            f"{session_id} (status={status})"
        )


class DriverWorkSessionNotOpen(PickupError):
    """The requested transition needs an open work session in a specific state."""

    def __init__(self, *, session_id: str, status: str, required: str) -> None:
        self.session_id = session_id
        self.status = status
        self.required = required
        super().__init__(
            f"work session {session_id} is {status}; {required} required"
        )


class DriverWorkSessionBlocked(PickupError):
    """Operational blockers prevent pausing or ending the work session."""

    def __init__(self, *, session_id: str, blockers: tuple[str, ...]) -> None:
        self.session_id = session_id
        self.blockers = blockers
        joined = ", ".join(blockers)
        super().__init__(f"work session {session_id} blocked by: {joined}")


class DriverNotAvailableForAssignment(PickupError):
    """A driver must hold an ONLINE pickup work session to take new work."""

    def __init__(self, *, driver_user_id: str, availability: str) -> None:
        self.driver_user_id = driver_user_id
        self.availability = availability
        super().__init__(
            f"driver {driver_user_id} is {availability}; ONLINE required for assignment"
        )


class InvalidWorkSessionReason(PickupError):
    """Reason code missing or not allowed for this transition."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class AssignmentAlreadyResolved(PickupError):
    """The assignment offer was already acknowledged or declined."""

    def __init__(self, *, pickup_task_id: str, assignment_state: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.assignment_state = assignment_state
        super().__init__(
            f"pickup task {pickup_task_id} assignment already {assignment_state}"
        )


class AssignmentNotAcknowledged(PickupError):
    """Driver progress requires an acknowledged assignment."""

    def __init__(self, *, pickup_task_id: str, assignment_state: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.assignment_state = assignment_state
        super().__init__(
            f"pickup task {pickup_task_id} assignment is {assignment_state}; "
            "ACKNOWLEDGED required"
        )


class InvalidTaskTransition(PickupError):
    """Driver task progress is forward-only through the declared lifecycle."""

    def __init__(self, *, pickup_task_id: str, current_status: str, target_status: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"pickup task {pickup_task_id} cannot move {current_status} -> {target_status}"
        )


class ScannedIdentifierMismatch(PickupError):
    """The scanned identifier does not match the task's expected parcel identity."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"scanned identifier does not match pickup task {pickup_task_id}")


class ExceptionEvidenceInsufficient(PickupError):
    """The reported exception reason requires more context than was supplied."""

    def __init__(self, *, reason: str, required: str) -> None:
        self.reason = reason
        self.required = required
        super().__init__(f"exception reason {reason} requires {required}")


class CourierChallengeNotFound(PickupError):
    """No active courier challenge for this pickup task."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"no active courier challenge for pickup task {pickup_task_id}")


class CourierChallengeIssueBlocked(PickupError):
    """A new challenge cannot be issued in the current task or ceremony state."""

    def __init__(self, *, pickup_task_id: str, detail: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.detail = detail
        super().__init__(f"cannot issue courier challenge for {pickup_task_id}: {detail}")


class CourierChallengeExpired(PickupError):
    """The challenge display window has elapsed."""

    def __init__(self, *, challenge_id: str) -> None:
        self.challenge_id = challenge_id
        super().__init__(f"courier challenge {challenge_id} expired")


class CourierChallengeInvalidated(PickupError):
    """The challenge was invalidated — usually by reassignment or cancellation."""

    def __init__(self, *, challenge_id: str, reason: str | None) -> None:
        self.challenge_id = challenge_id
        self.reason = reason
        super().__init__(f"courier challenge {challenge_id} invalidated (reason={reason})")


class CourierChallengeAlreadyUsed(PickupError):
    """The challenge was already consumed by a committed acceptance."""

    def __init__(self, *, challenge_id: str) -> None:
        self.challenge_id = challenge_id
        super().__init__(f"courier challenge {challenge_id} already used")


class CourierSecretInvalid(PickupError):
    """The presented challenge secret did not verify."""

    def __init__(self, *, challenge_id: str) -> None:
        self.challenge_id = challenge_id
        super().__init__(f"courier challenge {challenge_id} secret invalid")


class CourierVerificationRateLimited(PickupError):
    """Too many failed verification attempts — challenge is locked out."""

    def __init__(self, *, challenge_id: str, locked_until: str) -> None:
        self.challenge_id = challenge_id
        self.locked_until = locked_until
        super().__init__(
            f"courier challenge {challenge_id} locked until {locked_until}"
        )


class CourierVerificationRequired(PickupError):
    """Acceptance requires a live verified challenge."""

    def __init__(self, *, pickup_task_id: str, detail: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.detail = detail
        super().__init__(
            f"pickup task {pickup_task_id} requires courier verification: {detail}"
        )


class CourierManifestRequired(PickupError):
    """Acceptance requires a sender-confirmed parcel manifest."""

    def __init__(self, *, pickup_task_id: str, detail: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.detail = detail
        super().__init__(
            f"pickup task {pickup_task_id} requires a confirmed manifest: {detail}"
        )


class CourierManifestMismatch(PickupError):
    """The confirmed manifest does not describe the parcel being accepted."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"manifest does not match pickup task {pickup_task_id}")


class CourierManifestNotSubmitted(PickupError):
    """Confirmation requires a submitted manifest."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"no submitted manifest for pickup task {pickup_task_id}")


class SenderMayNotVerifyOwnCourier(PickupError):
    """The assigned courier may never verify itself as the sender."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(
            f"assigned courier cannot self-verify pickup task {pickup_task_id}"
        )


class HandoverManifestNotFound(PickupError):
    """Hub handover manifest does not exist."""

    def __init__(self, *, manifest_id: str) -> None:
        self.manifest_id = manifest_id
        super().__init__(f"handover manifest not found: {manifest_id}")


class HandoverManifestNotMutable(PickupError):
    """The manifest reached a terminal state and cannot change."""

    def __init__(self, *, manifest_id: str, status: str) -> None:
        self.manifest_id = manifest_id
        self.status = status
        super().__init__(f"handover manifest {manifest_id} is {status}")


class HandoverManifestEmpty(PickupError):
    """A hub handover manifest must carry at least one parcel."""

    def __init__(self) -> None:
        super().__init__("handover manifest requires at least one pickup task")


class HandoverManifestItemNotFound(PickupError):
    """No manifest line for this shipment."""

    def __init__(self, *, manifest_id: str, shipment_id: str) -> None:
        self.manifest_id = manifest_id
        self.shipment_id = shipment_id
        super().__init__(
            f"handover manifest {manifest_id} has no line for shipment {shipment_id}"
        )


class HandoverManifestNotArrived(PickupError):
    """Hub receipt requires the driver to have arrived at the hub."""

    def __init__(self, *, manifest_id: str, status: str) -> None:
        self.manifest_id = manifest_id
        self.status = status
        super().__init__(
            f"handover manifest {manifest_id} is {status}; hub arrival required"
        )


class ShipmentNotInDriverCustody(PickupError):
    """Only parcels the driver actually holds may be placed on a handover manifest."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(
            f"pickup task {pickup_task_id} is not in accepted pickup-driver custody"
        )


class ShipmentAlreadyOnActiveManifest(PickupError):
    """A parcel may sit on only one active hub handover manifest."""

    def __init__(self, *, shipment_id: str, manifest_id: str) -> None:
        self.shipment_id = shipment_id
        self.manifest_id = manifest_id
        super().__init__(
            f"shipment {shipment_id} already on active handover manifest {manifest_id}"
        )


class HubScopeNotAuthorized(PickupError):
    """The receiving actor is not scoped to this hub."""

    def __init__(self, *, hub_id: str) -> None:
        self.hub_id = hub_id
        super().__init__(f"actor is not authorized to receive for hub {hub_id}")


class OfflineAuthorizationNotFound(PickupError):
    """Offline authorization is unknown, not ours, or not bound to this device."""

    def __init__(self) -> None:
        super().__init__("offline authorization is not recognized")


class OfflineAuthorizationRevoked(PickupError):
    """Offline authorization was revoked before sync."""

    def __init__(self, *, reason: str | None) -> None:
        self.reason = reason
        super().__init__(f"offline authorization revoked (reason={reason})")


class OfflineSyncDeadlinePassed(PickupError):
    """Offline captures were submitted after the sync deadline."""

    def __init__(self, *, sync_deadline: str) -> None:
        self.sync_deadline = sync_deadline
        super().__init__(f"offline sync deadline passed at {sync_deadline}")


class OfflineAuthorizationInvalid(PickupError):
    """Offline authorization token failed signature or claim validation."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class OfflineBatchTooLarge(PickupError):
    """Offline sync batch exceeds the configured event limit."""

    def __init__(self, *, max_events: int) -> None:
        self.max_events = max_events
        super().__init__(f"offline sync batch exceeds {max_events} events")


class OfflineOperationNotAuthorized(PickupError):
    """Requested offline operation is outside the authorization scope."""

    def __init__(self, *, operations: tuple[str, ...]) -> None:
        self.operations = operations
        joined = ", ".join(operations)
        super().__init__(f"offline operations not authorized: {joined}")


class ReconciliationCaseNotFound(PickupError):
    """Reconciliation case does not exist."""

    def __init__(self, *, case_id: str) -> None:
        self.case_id = case_id
        super().__init__(f"reconciliation case not found: {case_id}")


class ReconciliationCaseAlreadyResolved(PickupError):
    """Reconciliation case was already resolved."""

    def __init__(self, *, case_id: str) -> None:
        self.case_id = case_id
        super().__init__(f"reconciliation case already resolved: {case_id}")


class SigningKeyUnavailable(PickupError):
    """Handover and offline features need a configured signing key — fail closed."""

    def __init__(self) -> None:
        super().__init__("pickup signing key is not configured")


class PackagingDecisionNotPermitted(PickupError):
    """The decision the driver chose is not one this packaging assessment allows.

    The binding case is ``TOO_WEAK``: packaging that will not survive handling can only be
    refused, never accepted with a warning or a note.
    """

    def __init__(self, *, assessment: str, decision: str, permitted: tuple[str, ...]) -> None:
        self.assessment = assessment
        self.decision = decision
        self.permitted = permitted
        allowed = ", ".join(permitted)
        super().__init__(
            f"packaging assessment {assessment} does not permit decision {decision} "
            f"(permitted: {allowed})"
        )


class PickupPhotoDocumentationMissing(PickupError):
    """Acceptance is blocked until the required pickup photo is attached."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(
            f"pickup task {pickup_task_id} requires photo documentation before acceptance"
        )


class StopOutcomeAlreadyRecorded(PickupError):
    """This expected parcel already has a different terminal stop outcome."""

    def __init__(self, *, pickup_task_id: str, stop_outcome: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.stop_outcome = stop_outcome
        super().__init__(
            f"pickup task {pickup_task_id} already resolved as {stop_outcome}"
        )


class StopOutcomeNotAllowed(PickupError):
    """A parcel already in HUDHUD custody can no longer be refused or marked not presented."""

    def __init__(self, *, pickup_task_id: str, reason: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.reason = reason
        super().__init__(f"stop outcome not allowed for {pickup_task_id}: {reason}")


class ScanIdentifierMissing(PickupError):
    """A scan resolution needs a scanned label; codes are never typed in by hand."""

    def __init__(self) -> None:
        super().__init__("scanned identifier is required")


class PickupBatchNotFound(PickupError):
    """No pickup task is assigned to this driver under that batch."""

    def __init__(self, *, batch_id: str) -> None:
        self.batch_id = batch_id
        super().__init__(f"pickup batch not found: {batch_id}")
