"""Driver workforce domain for the Pickup capability.

Pickup owns only the *pickup* capability availability of a driver. Driver identity,
roles, and cross-capability attendance remain with `auth_identity` (ADR-0004) and are
consumed through the authorization port — never stored here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DriverCapability(StrEnum):
    """Capability scope owned by this service."""

    PICKUP = "PICKUP"


class DriverAvailabilityStatus(StrEnum):
    """Server-derived ability to receive new pickup work."""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ON_BREAK = "ON_BREAK"


class WorkSessionStatus(StrEnum):
    """Driver work-session lifecycle."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


class WorkSessionPauseReason(StrEnum):
    BREAK = "BREAK"
    VEHICLE_ISSUE = "VEHICLE_ISSUE"
    PRAYER = "PRAYER"
    PERSONAL = "PERSONAL"
    OPERATIONS_REQUEST = "OPERATIONS_REQUEST"
    OTHER = "OTHER"


class WorkSessionEndReason(StrEnum):
    SESSION_COMPLETE = "SESSION_COMPLETE"
    OPERATIONS_REQUEST = "OPERATIONS_REQUEST"
    VEHICLE_ISSUE = "VEHICLE_ISSUE"
    PERSONAL = "PERSONAL"
    OTHER = "OTHER"


class WorkSessionBlocker(StrEnum):
    """Operational reasons a driver may not pause or end a pickup work session."""

    OPEN_PICKUP_CUSTODY = "OPEN_PICKUP_CUSTODY"
    ACTIVE_HANDOVER_MANIFEST = "ACTIVE_HANDOVER_MANIFEST"
    ACTIVE_ASSIGNED_TASK = "ACTIVE_ASSIGNED_TASK"
    UNSYNCED_OFFLINE_WORK = "UNSYNCED_OFFLINE_WORK"


OPEN_WORK_SESSION_STATUSES: frozenset[WorkSessionStatus] = frozenset(
    {WorkSessionStatus.ACTIVE, WorkSessionStatus.PAUSED}
)


@dataclass(slots=True)
class DriverWorkSession:
    """One driver's pickup-capability availability window."""

    session_id: UUID
    driver_user_id: str
    capability: DriverCapability
    status: WorkSessionStatus
    availability: DriverAvailabilityStatus
    started_at: datetime
    home_hub_id: UUID | None = None
    paused_at: datetime | None = None
    resumed_at: datetime | None = None
    ended_at: datetime | None = None
    pause_reason: WorkSessionPauseReason | None = None
    end_reason: WorkSessionEndReason | None = None
    notes: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    version: int = 1

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_WORK_SESSION_STATUSES

    @property
    def can_receive_assignment(self) -> bool:
        return (
            self.status is WorkSessionStatus.ACTIVE
            and self.availability is DriverAvailabilityStatus.ONLINE
        )


def derive_availability(status: WorkSessionStatus) -> DriverAvailabilityStatus:
    """Availability is derived from session state — never client-asserted."""
    if status is WorkSessionStatus.ACTIVE:
        return DriverAvailabilityStatus.ONLINE
    if status is WorkSessionStatus.PAUSED:
        return DriverAvailabilityStatus.ON_BREAK
    return DriverAvailabilityStatus.OFFLINE
