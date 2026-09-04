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
