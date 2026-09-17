"""Hub aggregates: hub, drop-off, parcel presence, consignment, linehaul, holds."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from hub.domain.value_objects import (
    CONSIGNMENT_TRANSITIONS,
    DROP_OFF_TRANSITIONS,
    TAMPER_OUTCOMES,
    ConsignmentStatus,
    CutOffTime,
    DropOffStatus,
    GeoPoint,
    HoldReason,
    LinehaulStatus,
    ParcelDisposition,
    ParcelPresenceStatus,
    PositionSource,
    RoutingDecision,
    SealCheckOutcome,
    Urgency,
)


@dataclass(slots=True)
class Hub:
    """One facility, with its own daily cut-off (v6.3 p.23)."""

    hub_id: UUID
    code: str
    name: str
    governorate: str
    cut_off: CutOffTime
    is_active: bool = True
    #: v6.3 p.24 — "Hudhud can add cameras inside inter-city transport vehicles." Whether
    #: a given hub's fleet is fitted is operational fact, recorded rather than assumed.
    vehicle_cameras_fitted: bool = False
    version: int = 1


@dataclass(slots=True)
class DropOff:
    """A regular customer handing a parcel in at a hub (CUS-03 … CUS-07).

    ``weight_grams`` and ``label_code`` are filled in *by hub staff*, not by the customer:
    v6.3 p.18 is explicit that "Hub staff — not the customer — stick a label onto the
    parcel and scan it".
    """

    drop_off_id: UUID
    hub_id: UUID
    tracking_code: str
    status: DropOffStatus
    shipment_request_id: UUID | None = None
    sender_principal_id: UUID | None = None
    #: Details taken at the counter when the customer "arrives with nothing".
    captured_details: dict[str, str] = field(default_factory=dict)
    weight_grams: int | None = None
    label_code: str | None = None
    labelled_by_actor_id: UUID | None = None
    created_at: datetime | None = None
    #: CUS-06 — "Unclaimed orders are cancelled after 3 days."
    expires_at: datetime | None = None
    accepted_at: datetime | None = None
    accepted_by_actor_id: UUID | None = None
    closed_at: datetime | None = None
    version: int = 1

    def can_transition_to(self, target: DropOffStatus) -> bool:
        return target in DROP_OFF_TRANSITIONS[self.status]

    @property
    def is_open(self) -> bool:
        return bool(DROP_OFF_TRANSITIONS[self.status])

    @property
    def has_label(self) -> bool:
        return self.label_code is not None

    def is_expired_at(self, moment: datetime) -> bool:
        """Only an unlabelled drop-off can lapse: a labelled one is on the counter."""
        if self.expires_at is None or self.status not in {
            DropOffStatus.EXPECTED,
            DropOffStatus.DETAILS_CAPTURED,
        }:
            return False
        return moment >= self.expires_at


@dataclass(slots=True)
class ParcelPresence:
    """One parcel's state inside one hub (v6.3 p.22, Figure 8).

    Hub owns where a parcel is *within its walls*; Shipment owns the canonical custody
    record. This aggregate publishes facts rather than writing Shipment's lifecycle.
    """

    presence_id: UUID
    hub_id: UUID
    tracking_code: str
    status: ParcelPresenceStatus
    destination_governorate: str
    urgency: Urgency = Urgency.STANDARD
    routing_decision: RoutingDecision | None = None
    route_code: str | None = None
    consignment_id: UUID | None = None
    received_at: datetime | None = None
    sorted_at: datetime | None = None
    departed_at: datetime | None = None
    handed_to_last_mile_at: datetime | None = None
    hold_reason: HoldReason | None = None
    disposition: ParcelDisposition | None = None
    version: int = 1

    @property
    def is_held(self) -> bool:
        return self.status is ParcelPresenceStatus.HELD

    @property
    def is_same_city(self) -> bool:
        return self.routing_decision is RoutingDecision.SAME_CITY_DIRECT

    @property
    def awaiting_linehaul(self) -> bool:
        return (
            self.routing_decision is RoutingDecision.INTER_CITY_LINEHAUL
            and self.status in {ParcelPresenceStatus.SORTED, ParcelPresenceStatus.GROUPED}
        )


@dataclass(slots=True)
class SecuritySeal:
    """A hub-level seal on a group of parcels — optional, at the hub's discretion (p.22).

    Distinct from the per-parcel packaging seal a merchant buys (v6.3 p.14); v6.3 says so
    explicitly, and conflating them would make a merchant's add-on look like hub security.
    """

    seal_code: str
    applied_at: datetime
    applied_by_actor_id: UUID


@dataclass(slots=True)
class Consignment:
    """Parcels grouped for one inter-city move (v6.3 p.22)."""

    consignment_id: UUID
    origin_hub_id: UUID
    destination_hub_id: UUID
    status: ConsignmentStatus = ConsignmentStatus.OPEN
    seal: SecuritySeal | None = None
    parcel_codes: tuple[str, ...] = ()
    created_at: datetime | None = None
    dispatched_at: datetime | None = None
    arrived_at: datetime | None = None
    reconciled_at: datetime | None = None
    version: int = 1

    def can_transition_to(self, target: ConsignmentStatus) -> bool:
        return target in CONSIGNMENT_TRANSITIONS[self.status]

    @property
    def is_sealed(self) -> bool:
        return self.seal is not None

    @property
    def parcel_count(self) -> int:
        return len(self.parcel_codes)


@dataclass(slots=True)
class SealCheck:
    """The destination hub's check of a seal it did not apply (v6.3 p.25)."""

    check_id: UUID
    consignment_id: UUID
    hub_id: UUID
    expected_seal_code: str
    observed_seal_code: str | None
    outcome: SealCheckOutcome
    checked_at: datetime
    checked_by_actor_id: UUID

    @property
    def opens_investigation(self) -> bool:
        return self.outcome in TAMPER_OUTCOMES


@dataclass(slots=True)
class VehiclePosition:
    """One location fix, attributed to the source that produced it (v6.3 p.24)."""

    position_id: UUID
    linehaul_id: UUID
    source: PositionSource
    point: GeoPoint
    recorded_at: datetime


@dataclass(slots=True)
class Linehaul:
    """One overnight inter-city move (v6.3 p.23, p.24)."""

    linehaul_id: UUID
    consignment_id: UUID
    origin_hub_id: UUID
    destination_hub_id: UUID
    vehicle_reference: str
    driver_principal_id: UUID
    status: LinehaulStatus = LinehaulStatus.PLANNED
    planned_departure_at: datetime | None = None
    departed_at: datetime | None = None
    expected_arrival_at: datetime | None = None
    arrived_at: datetime | None = None
    #: OPS-02 — the route is watched for major deviation and the ETA is kept current.
    route_deviation_flagged: bool = False
    deviation_note: str | None = None
    version: int = 1

    @property
    def is_moving(self) -> bool:
        return self.status is LinehaulStatus.DEPARTED

    def delay_minutes(self, moment: datetime) -> int | None:
        """How late this move is running, or ``None`` when there is nothing to compare."""
        if self.expected_arrival_at is None or self.arrived_at is not None:
            if self.arrived_at is not None and self.expected_arrival_at is not None:
                return max(
                    0,
                    int(
                        (self.arrived_at - self.expected_arrival_at).total_seconds() // 60
                    ),
                )
            return None
        if moment <= self.expected_arrival_at:
            return 0
        return int((moment - self.expected_arrival_at).total_seconds() // 60)


@dataclass(slots=True)
class HubActivitySnapshot:
    """The operations view of one hub (OPS-05): backlog, ready, delayed, missing."""

    hub_id: UUID
    received: int
    sorted_count: int
    grouped: int
    ready_for_last_mile: int
    held: int
    awaiting_linehaul: int
    delayed_linehauls: int

    @property
    def backlog(self) -> int:
        """Everything physically in the hub that has not moved on."""
        return self.received + self.sorted_count + self.grouped
