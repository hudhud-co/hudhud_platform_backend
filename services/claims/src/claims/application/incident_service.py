"""Driver incidents (DRV-P25) and the operations views over them (OPS-06, OPS-07).

Driver App v8 puts two sentences on the report screen and both are requirements:

* "The parcel stays in your custody while the report is open." — so an open incident
  about a parcel is a custody fact, not a note;
* "No amounts are shown or decided here." — with `incidentDone` adding "No compensation
  or claim value is shown to the driver" (SEC-07).

The second is enforced by there being no amount on a :class:`DriverIncident` at all, and
by the driver-facing summary having no field for one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from claims.domain.entities import (
    ClaimSummaryForDriver,
    DriverIncident,
    ReturnsAndClaimsRow,
)
from claims.domain.errors import (
    IncidentNeedsAParcel,
    IncidentNotFound,
    IncidentTransitionNotAllowed,
    InvalidTrackingCode,
    OnlyOperationsResolvesAnIncident,
    ResolutionNoteRequired,
)
from claims.domain.value_objects import (
    INCIDENTS_ABOUT_A_PARCEL,
    EvidenceMediaRef,
    IncidentKind,
    IncidentStatus,
    build_claim_reference,
    is_valid_tracking_code,
)
from claims.ports.repository import ClaimsUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


class IncidentService:
    def __init__(self, unit_of_work: ClaimsUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------------------- DRV-P25

    def report(
        self,
        *,
        kind: IncidentKind,
        driver_principal_id: UUID,
        tracking_code: str | None = None,
        note: str | None = None,
        evidence: tuple[EvidenceMediaRef, ...] = (),
    ) -> DriverIncident:
        """A driver reporting what happened. No amount is taken and none is returned.

        Every kind except a vehicle or safety issue is about one parcel — Driver App v8
        computes exactly that (`needsParcel`), and disables submission without one.
        """
        if kind in INCIDENTS_ABOUT_A_PARCEL:
            if tracking_code is None:
                raise IncidentNeedsAParcel(kind.value)
            if not is_valid_tracking_code(tracking_code):
                raise InvalidTrackingCode(tracking_code)

        self._uow.begin()
        try:
            moment = _now()
            day = moment.strftime("%Y%m%d")
            reference = build_claim_reference(
                day=day, sequence=self._uow.incidents.next_sequence_for_day(day)
            )
            incident = DriverIncident(
                incident_id=uuid4(),
                reference=reference,
                kind=kind,
                reported_by_driver_id=driver_principal_id,
                reported_at=moment,
                tracking_code=(
                    tracking_code if kind in INCIDENTS_ABOUT_A_PARCEL else None
                ),
                note=(note or "").strip() or None,
                evidence=evidence,
                status=IncidentStatus.SUBMITTED,
            )
            self._uow.incidents.save(incident)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return incident

    # ------------------------------------------------------------- OPS-07

    def start_investigation(self, *, reference: str, actor) -> DriverIncident:
        if not actor.may_resolve_an_incident:
            raise OnlyOperationsResolvesAnIncident()
        return self._advance(
            reference=reference,
            target=IncidentStatus.UNDER_INVESTIGATION,
            actor=actor,
        )

    def resolve(
        self, *, reference: str, actor, note: str, linked_claim_id: UUID | None = None
    ) -> DriverIncident:
        """OPS-07 — operations closes it, and says what was found.

        ``linked_claim_id`` is how an incident becomes a claim when it should: the
        incident records that a claim exists, and the claim carries the money. The
        incident still has no amount on it.
        """
        if not actor.may_resolve_an_incident:
            raise OnlyOperationsResolvesAnIncident()
        if not note.strip():
            raise ResolutionNoteRequired()
        return self._advance(
            reference=reference,
            target=IncidentStatus.RESOLVED,
            actor=actor,
            note=note.strip(),
            linked_claim_id=linked_claim_id,
        )

    # ------------------------------------------------------------- SEC-07

    def for_driver(self, *, reference: str) -> ClaimSummaryForDriver:
        """What the driver who reported it may see.

        `incidentDone` shows the reference, the type, the parcel, the time and the
        status — and nothing else. This returns exactly that shape, which has no field
        an amount could be put in.
        """
        self._uow.begin()
        try:
            incident = self._require(reference)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return ClaimSummaryForDriver(
            reference=incident.reference,
            kind=incident.kind.value,
            status=incident.status.value,
            tracking_code=incident.tracking_code,
            submitted_at=incident.reported_at,
            resolved_at=incident.resolved_at,
        )

    def my_incidents(
        self, *, driver_principal_id: UUID
    ) -> tuple[ClaimSummaryForDriver, ...]:
        self._uow.begin()
        try:
            found = self._uow.incidents.list_for_driver(driver_principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(
            ClaimSummaryForDriver(
                reference=incident.reference,
                kind=incident.kind.value,
                status=incident.status.value,
                tracking_code=incident.tracking_code,
                submitted_at=incident.reported_at,
                resolved_at=incident.resolved_at,
            )
            for incident in found
        )

    def parcels_held_by_open_incidents(self) -> tuple[str, ...]:
        """Which parcels must not move: "the parcel stays in your custody"."""
        self._uow.begin()
        try:
            open_ones = self._uow.incidents.list_open()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(
            incident.tracking_code
            for incident in open_ones
            if incident.parcel_stays_in_custody and incident.tracking_code
        )

    # ------------------------------------------------------------- OPS-06

    def returns_and_claims(self, *, actor) -> tuple[ReturnsAndClaimsRow, ...]:
        """OPS-06 — the operations view over claims and driver incidents together.

        Decided claims appear alongside open ones: an operator who can only see the
        work still to do can never answer "what did we pay on this one?". That is also
        what the amount filter below is for — the compensation figure appears only for
        an actor who may see one (SEC-07), so an operator who is also a driver sees the
        same rows without the amounts rather than being refused the view.
        """
        show_amounts = actor.may_see_a_compensation_value
        self._uow.begin()
        try:
            claims = self._uow.claims.list_for_operations_view()
            incidents = self._uow.incidents.list_open()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        rows = [
            ReturnsAndClaimsRow(
                reference=claim.reference,
                tracking_code=claim.tracking_code,
                kind=claim.kind.value,
                status=claim.status.value,
                opened_by=claim.opened_by.value,
                submitted_at=claim.submitted_at,
                awaiting_operations=claim.is_open,
                compensation_amount=(
                    claim.compensation_amount if show_amounts else None
                ),
            )
            for claim in claims
        ]
        rows.extend(
            ReturnsAndClaimsRow(
                reference=incident.reference,
                tracking_code=incident.tracking_code,
                kind=incident.kind.value,
                status=incident.status.value,
                opened_by="DRIVER",
                submitted_at=incident.reported_at,
                awaiting_operations=incident.is_open,
                # An incident never carries one, whoever is looking.
                compensation_amount=None,
                extra={"parcel_held": str(incident.parcel_stays_in_custody).lower()},
            )
            for incident in incidents
        )
        return tuple(
            sorted(rows, key=lambda row: row.submitted_at or datetime.min.replace(tzinfo=UTC))
        )

    # ------------------------------------------------------------- internals

    def _advance(
        self,
        *,
        reference: str,
        target: IncidentStatus,
        actor,
        note: str | None = None,
        linked_claim_id: UUID | None = None,
    ) -> DriverIncident:
        self._uow.begin()
        try:
            incident = self._require(reference)
            if not incident.can_transition_to(target):
                raise IncidentTransitionNotAllowed(incident.status.value, target.value)
            moment = _now()
            incident.status = target
            if target is IncidentStatus.UNDER_INVESTIGATION:
                incident.investigation_started_at = moment
            if target is IncidentStatus.RESOLVED:
                incident.resolved_at = moment
                incident.resolved_by_actor_id = actor.principal_id
                incident.resolution_note = note
                incident.linked_claim_id = linked_claim_id
            incident.version += 1
            self._uow.incidents.save(incident)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return incident

    def _require(self, reference: str) -> DriverIncident:
        incident = self._uow.incidents.find_by_reference(reference)
        if incident is None:
            raise IncidentNotFound(reference)
        return incident
