"""Shipment eligibility port — Pickup-local facts only; production adapter deferred."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pickup.domain.value_objects import CustodyType, ShipmentStatus


@dataclass(frozen=True, slots=True)
class ShipmentEligibilitySnapshot:
    """Minimal Shipment facts for recovery eligibility checks.

    Recovery authorization input: ``custody_type`` only — block when it is
    ``PICKUP_DRIVER``. ``shipment_status``, ``custody_started``, and
    ``custody_id`` are retained for adapter/backward compatibility and are
    **not** recovery authorization inputs.
    """

    shipment_id: UUID
    shipment_status: ShipmentStatus
    custody_started: bool
    custody_type: CustodyType | None
    custody_id: str | None

    @property
    def custody_owner_present(self) -> bool:
        """Compatibility helper — not a recovery authorization input."""
        return self.custody_id is not None


class ShipmentEligibilityPort(Protocol):
    """Read-only Shipment eligibility boundary — no Shipment database access."""

    @property
    def is_production_ready(self) -> bool:
        """True when a real Shipment HTTP/event adapter is configured (not deferred)."""

    def get_eligibility(self, shipment_id: UUID) -> ShipmentEligibilitySnapshot | None: ...
