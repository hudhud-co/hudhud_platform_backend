"""Consignments, the per-hub cut-off, seal checks and the inter-city move.

Three v6.3 rules live here and nowhere else:

* **SHP-07** — inter-city parcels move overnight, after *that hub's own* cut-off. v6.3
  p.23 changed this in 6.3: "set per hub, not company-wide".
* **SHP-08** — the destination hub checks a seal it did not apply, and a mismatch "opens a
  tamper investigation, never a silent pass-through" (p.25).
* the linehaul driver's role boundary (p.24): they follow the route and report deviation,
  and cannot split a grouped batch early or deliver directly to a receiver.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hub.domain.entities import (
    Consignment,
    Hub,
    Linehaul,
    SealCheck,
    SecuritySeal,
    VehiclePosition,
)
from hub.domain.errors import (
    ConsignmentAlreadySealed,
    ConsignmentIsEmpty,
    ConsignmentNotFound,
    ConsignmentTransitionNotAllowed,
    CutOffNotReached,
    HubNotActive,
    HubNotFound,
    LinehaulDriverMayNotSplitABatch,
    LinehaulNotFound,
    LinehaulTransitionNotAllowed,
    ParcelIsHeld,
    ParcelNotInThisHub,
    ParcelNotSorted,
    SameCityParcelDoesNotTravelBetweenHubs,
    SealCodeInvalid,
    SealMismatchRequiresInvestigation,
    UnsealedConsignmentHasNothingToCheck,
)
from hub.domain.value_objects import (
    ConsignmentStatus,
    GeoPoint,
    LinehaulStatus,
    ParcelPresenceStatus,
    PositionSource,
    SealCheckOutcome,
    normalize_seal_code,
)
from hub.ports.repository import HubUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class SealCheckResult:
    check: SealCheck
    consignment: Consignment
    opened_investigation: bool


class LinehaulService:
    def __init__(self, unit_of_work: HubUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------------------- consignment

    def open_consignment(
        self, *, origin_hub_id: UUID, destination_hub_id: UUID
    ) -> Consignment:
        self._uow.begin()
        try:
            self._require_active_hub(origin_hub_id)
            self._require_active_hub(destination_hub_id)
            consignment = Consignment(
                consignment_id=uuid4(),
                origin_hub_id=origin_hub_id,
                destination_hub_id=destination_hub_id,
                status=ConsignmentStatus.OPEN,
                created_at=_now(),
            )
            self._uow.consignments.save(consignment)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return consignment

    def add_parcel(self, *, consignment_id: UUID, tracking_code: str) -> Consignment:
        """Group a sorted parcel for the move (v6.3 p.22).

        A same-city parcel is refused here: its hub-to-hub stage is skipped entirely, so
        putting it on a linehaul would send it away from its own destination.
        """
        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            if consignment.status is not ConsignmentStatus.OPEN:
                raise ConsignmentTransitionNotAllowed(
                    consignment.status.value, "grouped"
                )
            presence = self._uow.presences.find_in_hub(
                consignment.origin_hub_id, tracking_code
            )
            if presence is None:
                raise ParcelNotInThisHub(tracking_code)
            if presence.is_held:
                reason = presence.hold_reason.value if presence.hold_reason else "unknown"
                raise ParcelIsHeld(tracking_code, reason)
            if presence.is_same_city:
                raise SameCityParcelDoesNotTravelBetweenHubs(tracking_code)
            if presence.status is not ParcelPresenceStatus.SORTED:
                raise ParcelNotSorted(tracking_code)

            if tracking_code not in consignment.parcel_codes:
                consignment.parcel_codes = (*consignment.parcel_codes, tracking_code)
                consignment.version += 1
                self._uow.consignments.save(consignment)

            presence.status = ParcelPresenceStatus.GROUPED
            presence.consignment_id = consignment.consignment_id
            presence.version += 1
            self._uow.presences.save(presence)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return consignment

    def apply_seal(
        self, *, consignment_id: UUID, seal_code: str, operator_principal_id: UUID
    ) -> Consignment:
        """Optional, at the hub's discretion (v6.3 p.22)."""
        try:
            code = normalize_seal_code(seal_code)
        except ValueError as exc:
            raise SealCodeInvalid(seal_code) from exc

        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            if consignment.is_sealed:
                raise ConsignmentAlreadySealed(consignment.seal.seal_code)  # type: ignore[union-attr]
            if not consignment.can_transition_to(ConsignmentStatus.SEALED):
                raise ConsignmentTransitionNotAllowed(
                    consignment.status.value, ConsignmentStatus.SEALED.value
                )
            consignment.seal = SecuritySeal(
                seal_code=code,
                applied_at=_now(),
                applied_by_actor_id=operator_principal_id,
            )
            consignment.status = ConsignmentStatus.SEALED
            consignment.version += 1
            self._uow.consignments.save(consignment)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return consignment

    # ------------------------------------------------------------- dispatch

    def dispatch(
        self,
        *,
        consignment_id: UUID,
        moment: datetime | None = None,
        override_cut_off: bool = False,
    ) -> Consignment:
        """Send the consignment, after the origin hub's own cut-off (SHP-07).

        ``override_cut_off`` exists because operations sometimes must move a consignment
        early; it is an explicit, auditable choice rather than a silent bypass.
        """
        when = moment or _now()
        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            if not consignment.parcel_codes:
                raise ConsignmentIsEmpty()
            if not consignment.can_transition_to(ConsignmentStatus.DISPATCHED):
                raise ConsignmentTransitionNotAllowed(
                    consignment.status.value, ConsignmentStatus.DISPATCHED.value
                )
            facility = self._require_active_hub(consignment.origin_hub_id)
            if not override_cut_off and not facility.cut_off.has_passed(when.time()):
                raise CutOffNotReached(
                    facility.code, facility.cut_off.local_time.isoformat(timespec="minutes")
                )

            consignment.status = ConsignmentStatus.DISPATCHED
            consignment.dispatched_at = when
            consignment.version += 1
            self._uow.consignments.save(consignment)

            for presence in self._uow.presences.list_for_consignment(consignment_id):
                presence.status = ParcelPresenceStatus.DEPARTED
                presence.departed_at = when
                presence.version += 1
                self._uow.presences.save(presence)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return consignment

    # ------------------------------------------------------------- arrival

    def record_arrival(
        self, *, consignment_id: UUID, moment: datetime | None = None
    ) -> Consignment:
        when = moment or _now()
        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            if not consignment.can_transition_to(ConsignmentStatus.ARRIVED):
                raise ConsignmentTransitionNotAllowed(
                    consignment.status.value, ConsignmentStatus.ARRIVED.value
                )
            consignment.status = ConsignmentStatus.ARRIVED
            consignment.arrived_at = when
            consignment.version += 1
            self._uow.consignments.save(consignment)

            linehaul = self._uow.linehauls.find_for_consignment(consignment_id)
            if linehaul is not None and linehaul.status is LinehaulStatus.DEPARTED:
                linehaul.status = LinehaulStatus.ARRIVED
                linehaul.arrived_at = when
                linehaul.version += 1
                self._uow.linehauls.save(linehaul)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return consignment

    def check_seal(
        self,
        *,
        consignment_id: UUID,
        observed_seal_code: str | None,
        operator_principal_id: UUID,
        moment: datetime | None = None,
    ) -> SealCheckResult:
        """SHP-08 — check the seal, and stop the consignment if it does not match."""
        when = moment or _now()
        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            if consignment.seal is None:
                raise UnsealedConsignmentHasNothingToCheck()
            expected = consignment.seal.seal_code
            observed = (observed_seal_code or "").strip().upper() or None

            if observed is None:
                outcome = SealCheckOutcome.MISSING
            elif observed == expected:
                outcome = SealCheckOutcome.INTACT
            else:
                outcome = SealCheckOutcome.MISMATCHED

            check = SealCheck(
                check_id=uuid4(),
                consignment_id=consignment_id,
                hub_id=consignment.destination_hub_id,
                expected_seal_code=expected,
                observed_seal_code=observed,
                outcome=outcome,
                checked_at=when,
                checked_by_actor_id=operator_principal_id,
            )
            self._uow.seal_checks.save(check)

            if check.opens_investigation:
                consignment.status = ConsignmentStatus.UNDER_TAMPER_INVESTIGATION
                consignment.version += 1
                self._uow.consignments.save(consignment)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return SealCheckResult(
            check=check,
            consignment=consignment,
            opened_investigation=check.opens_investigation,
        )

    def reconcile(self, *, consignment_id: UUID) -> Consignment:
        """Close a clean arrival. Refuses a consignment under investigation."""
        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            if consignment.status is ConsignmentStatus.UNDER_TAMPER_INVESTIGATION:
                raise SealMismatchRequiresInvestigation(str(consignment_id))
            if not consignment.can_transition_to(ConsignmentStatus.RECONCILED):
                raise ConsignmentTransitionNotAllowed(
                    consignment.status.value, ConsignmentStatus.RECONCILED.value
                )
            consignment.status = ConsignmentStatus.RECONCILED
            consignment.reconciled_at = _now()
            consignment.version += 1
            self._uow.consignments.save(consignment)

            for presence in self._uow.presences.list_for_consignment(consignment_id):
                if presence.is_held:
                    continue
                presence.hub_id = consignment.destination_hub_id
                presence.status = ParcelPresenceStatus.READY_FOR_LAST_MILE
                presence.version += 1
                self._uow.presences.save(presence)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return consignment

    # ------------------------------------------------------------- linehaul

    def plan_linehaul(
        self,
        *,
        consignment_id: UUID,
        vehicle_reference: str,
        driver_principal_id: UUID,
        planned_departure_at: datetime | None = None,
        expected_arrival_at: datetime | None = None,
    ) -> Linehaul:
        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            linehaul = Linehaul(
                linehaul_id=uuid4(),
                consignment_id=consignment_id,
                origin_hub_id=consignment.origin_hub_id,
                destination_hub_id=consignment.destination_hub_id,
                vehicle_reference=vehicle_reference.strip(),
                driver_principal_id=driver_principal_id,
                status=LinehaulStatus.PLANNED,
                planned_departure_at=planned_departure_at,
                expected_arrival_at=expected_arrival_at,
            )
            self._uow.linehauls.save(linehaul)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return linehaul

    def depart(self, *, linehaul_id: UUID, moment: datetime | None = None) -> Linehaul:
        self._uow.begin()
        try:
            linehaul = self._load_linehaul(linehaul_id)
            if linehaul.status is not LinehaulStatus.PLANNED:
                raise LinehaulTransitionNotAllowed(
                    linehaul.status.value, LinehaulStatus.DEPARTED.value
                )
            linehaul.status = LinehaulStatus.DEPARTED
            linehaul.departed_at = moment or _now()
            linehaul.version += 1
            self._uow.linehauls.save(linehaul)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return linehaul

    def record_position(
        self,
        *,
        linehaul_id: UUID,
        source: PositionSource,
        point: GeoPoint,
        recorded_at: datetime | None = None,
    ) -> VehiclePosition:
        """OPS-01 — two independent sources, each recorded as itself."""
        self._uow.begin()
        try:
            self._load_linehaul(linehaul_id)
            position = VehiclePosition(
                position_id=uuid4(),
                linehaul_id=linehaul_id,
                source=source,
                point=point,
                recorded_at=recorded_at or _now(),
            )
            self._uow.positions.save(position)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return position

    def flag_deviation(
        self, *, linehaul_id: UUID, note: str, revised_arrival_at: datetime | None = None
    ) -> Linehaul:
        """OPS-02 — record a major deviation and keep the ETA current."""
        self._uow.begin()
        try:
            linehaul = self._load_linehaul(linehaul_id)
            linehaul.route_deviation_flagged = True
            linehaul.deviation_note = note.strip() or None
            if revised_arrival_at is not None:
                linehaul.expected_arrival_at = revised_arrival_at
            linehaul.version += 1
            self._uow.linehauls.save(linehaul)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return linehaul

    @staticmethod
    def assert_driver_may_not_split_batch() -> None:
        """v6.3 p.24 Role boundary — Linehaul Driver.

        Stated as an operation so that any future attempt to add batch-splitting to the
        driver's API has to delete this call rather than merely omit a check.
        """
        raise LinehaulDriverMayNotSplitABatch()

    def positions_for(self, *, linehaul_id: UUID) -> tuple[VehiclePosition, ...]:
        self._uow.begin()
        try:
            found = self._uow.positions.list_for_linehaul(linehaul_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def get_consignment(self, consignment_id: UUID) -> Consignment:
        self._uow.begin()
        try:
            consignment = self._load_consignment(consignment_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return consignment

    def get_linehaul(self, linehaul_id: UUID) -> Linehaul:
        self._uow.begin()
        try:
            linehaul = self._load_linehaul(linehaul_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return linehaul

    # ------------------------------------------------------------- internals

    def _load_consignment(self, consignment_id: UUID) -> Consignment:
        consignment = self._uow.consignments.get(consignment_id)
        if consignment is None:
            raise ConsignmentNotFound(str(consignment_id))
        return consignment

    def _load_linehaul(self, linehaul_id: UUID) -> Linehaul:
        linehaul = self._uow.linehauls.get(linehaul_id)
        if linehaul is None:
            raise LinehaulNotFound(str(linehaul_id))
        return linehaul

    def _require_active_hub(self, hub_id: UUID) -> Hub:
        facility = self._uow.hubs.get(hub_id)
        if facility is None:
            raise HubNotFound(str(hub_id))
        if not facility.is_active:
            raise HubNotActive(str(hub_id))
        return facility
