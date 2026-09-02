"""Domain errors for Shipment acceptance lifecycle."""

from __future__ import annotations


class ShipmentError(Exception):
    """Base Shipment domain error."""


class ShipmentNotFound(ShipmentError):
    """Requested shipment aggregate does not exist."""

    def __init__(self, shipment_id: str) -> None:
        self.shipment_id = shipment_id
        super().__init__(f"shipment not found: {shipment_id}")


class PickupTaskNotFound(ShipmentError):
    """Requested pickup task snapshot does not exist."""

    def __init__(self, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task not found: {pickup_task_id}")


class AcceptanceAlreadyRecorded(ShipmentError):
    """Acceptance decision was already recorded and cannot be overwritten."""

    def __init__(self, shipment_id: str) -> None:
        self.shipment_id = shipment_id
        super().__init__(f"acceptance already recorded for shipment: {shipment_id}")


class OptimisticConcurrencyConflict(ShipmentError):
    """Aggregate version changed since load — retry or reject stale command."""

    def __init__(
        self,
        *,
        entity_type: str,
        entity_id: str,
        expected_version: int,
    ) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.expected_version = expected_version
        super().__init__(
            f"stale {entity_type} {entity_id}: expected version {expected_version}"
        )


class InlineMediaNotAllowed(ShipmentError):
    """Evidence must be an external reference; inline media bytes are forbidden."""

    def __init__(self, detail: str = "inline media bytes are not allowed") -> None:
        self.detail = detail
        super().__init__(detail)


class ExceptionEvidenceRequired(ShipmentError):
    """Accepted-with-exception requires documented exception evidence references."""

    def __init__(self) -> None:
        super().__init__("accepted-with-exception requires exception evidence references")


class PickupTaskNotProofCaptured(ShipmentError):
    """Pickup task must be in PROOF_CAPTURED status before acceptance."""

    def __init__(self, *, pickup_task_id: str, current_status: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.current_status = current_status
        super().__init__(
            f"pickup task {pickup_task_id} not PROOF_CAPTURED (current={current_status})"
        )


class PickupTaskMissingAssignedDriver(ShipmentError):
    """Pickup task must have an assigned driver before acceptance."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task {pickup_task_id} missing assigned driver")


class PickupTaskMissingAssignedBatch(ShipmentError):
    """Pickup task must have an assigned batch before acceptance."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task {pickup_task_id} missing assigned batch")


class ActingDriverNotAssigned(ShipmentError):
    """Acting driver must match the pickup task assigned driver."""

    def __init__(self, *, pickup_task_id: str, acting_driver_user_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        self.acting_driver_user_id = acting_driver_user_id
        super().__init__(
            f"acting driver {acting_driver_user_id} is not assigned to pickup task "
            f"{pickup_task_id}"
        )


class ShipmentNotCreated(ShipmentError):
    """Shipment must be in CREATED status before acceptance."""

    def __init__(self, *, shipment_id: str, current_status: str) -> None:
        self.shipment_id = shipment_id
        self.current_status = current_status
        super().__init__(
            f"shipment {shipment_id} not CREATED (current={current_status})"
        )


class PickupConditionProofMissing(ShipmentError):
    """Pickup-condition proof must exist before acceptance."""

    def __init__(self, *, pickup_task_id: str) -> None:
        self.pickup_task_id = pickup_task_id
        super().__init__(f"pickup task {pickup_task_id} missing pickup-condition proof")


class ScannedIdentifierMismatch(ShipmentError):
    """Scanned shipment/waybill code must match the expected shipment identifier."""

    def __init__(self, *, shipment_id: str, scanned_identifier: str) -> None:
        self.shipment_id = shipment_id
        self.scanned_identifier = scanned_identifier
        super().__init__(
            f"scanned identifier {scanned_identifier!r} does not match shipment {shipment_id}"
        )
