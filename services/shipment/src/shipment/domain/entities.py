"""Core Shipment domain entities — order intent through acceptance scan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from shipment.domain.value_objects import (
    CustodyType,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ShipmentEventType,
    ShipmentStatus,
    WaybillIdentity,
)


@dataclass(slots=True)
class OrderIntent:
    """Order creation expresses intent only — no Hodhod custody or SLA (source §2, §3)."""

    order_id: UUID
    shipment_id: UUID
    created_at: datetime


@dataclass(slots=True)
class PickupTaskSnapshot:
    """Service-local Pickup prerequisite input — production adapter deferred (Phase 11)."""

    pickup_task_id: UUID
    shipment_id: UUID
    status: PickupTaskStatus
    assigned_driver_user_id: str | None
    assigned_batch_id: UUID | None
    has_pickup_condition_proof: bool
    acceptance_state: PickupTaskAcceptanceState | None = None


@dataclass(slots=True)
class Shipment:
    """Parcel aggregate bounded at acceptance scan — no post-acceptance lifecycle here."""

    shipment_id: UUID
    order_id: UUID
    waybill_identity: WaybillIdentity
    current_status: ShipmentStatus
    order_created_at: datetime
    accepted_at: datetime | None = None
    sla_started_at: datetime | None = None
    current_custody_type: CustodyType | None = None
    current_custody_id: str | None = None

    @property
    def custody_active(self) -> bool:
        return self.current_status is ShipmentStatus.IN_CUSTODY

    @property
    def sla_active(self) -> bool:
        return self.sla_started_at is not None


@dataclass(frozen=True, slots=True)
class ShipmentEvent:
    """Immutable shipment lifecycle event append-only record."""

    event_id: UUID
    shipment_id: UUID
    event_type: ShipmentEventType
    previous_status: ShipmentStatus
    new_status: ShipmentStatus
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AuditLogEntry:
    """Traceable audit record for acceptance decisions."""

    audit_id: UUID
    action: str
    entity_type: str
    entity_id: str
    actor_id: str
    occurred_at: datetime
    details: dict[str, str]
