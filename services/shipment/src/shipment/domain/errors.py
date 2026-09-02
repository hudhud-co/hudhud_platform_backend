"""Domain errors for Shipment acceptance lifecycle."""

from __future__ import annotations


class ShipmentError(Exception):
    """Base Shipment domain error."""


class ShipmentNotFound(ShipmentError):
    """Requested shipment aggregate does not exist."""

    def __init__(self, shipment_id: str) -> None:
        self.shipment_id = shipment_id
        super().__init__(f"shipment not found: {shipment_id}")


class AcceptanceAlreadyRecorded(ShipmentError):
    """Acceptance decision was already recorded and cannot be overwritten."""

    def __init__(self, shipment_id: str) -> None:
        self.shipment_id = shipment_id
        super().__init__(f"acceptance already recorded for shipment: {shipment_id}")


class InlineMediaNotAllowed(ShipmentError):
    """Evidence must be an external reference; inline media bytes are forbidden."""

    def __init__(self, detail: str = "inline media bytes are not allowed") -> None:
        self.detail = detail
        super().__init__(detail)


class ExceptionEvidenceRequired(ShipmentError):
    """Accepted-with-exception requires documented exception evidence references."""

    def __init__(self) -> None:
        super().__init__("accepted-with-exception requires exception evidence references")


class InvalidAcceptanceTransition(ShipmentError):
    """Lifecycle transition is not allowed at the current acceptance boundary."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
