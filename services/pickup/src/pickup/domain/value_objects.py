"""Value objects for Pickup recovery lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PickupTaskStatus(StrEnum):
    """Pickup task lifecycle status including recovery terminal states."""

    PENDING = "PENDING"
    PROOF_CAPTURED = "PROOF_CAPTURED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class PickupTaskAcceptanceState(StrEnum):
    """Pickup task acceptance outcome — aligned with Shipment W11 terminology."""

    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_EXCEPTION = "ACCEPTED_WITH_EXCEPTION"
    REJECTED = "REJECTED"


class RecoveryAction(StrEnum):
    """Distinct recovery business actions."""

    RETRY = "RETRY"
    RESCHEDULE = "RESCHEDULE"
    REASSIGN = "REASSIGN"
    CANCEL = "CANCEL"


class ShipmentStatus(StrEnum):
    """Minimal Shipment status facts for recovery eligibility — not Shipment authority."""

    CREATED = "CREATED"
    IN_CUSTODY = "IN_CUSTODY"


class CustodyType(StrEnum):
    """Custody holder type — canonical target terminology is PICKUP_DRIVER (ADR-0003 W17-A)."""

    PICKUP_DRIVER = "PICKUP_DRIVER"


@dataclass(frozen=True, slots=True)
class ScheduledWindow:
    """Optional pickup schedule window carried across recovery attempts."""

    start: datetime
    end: datetime
