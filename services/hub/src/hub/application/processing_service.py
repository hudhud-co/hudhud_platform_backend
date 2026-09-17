"""Hub processing: scan in, sort, group, hold and hand over (SHP-05, SHP-06, SHP-13).

v6.3 p.22 sets out the origin hub in four steps — scan in, sort by destination city,
urgency and route, group parcels heading the same way, and optionally seal the group. The
one branch that matters is the same-city case: "If a parcel is only travelling within the
same city, this hub-to-hub stage is skipped entirely."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hub.domain.entities import Hub, HubActivitySnapshot, ParcelPresence
from hub.domain.errors import (
    HubNotActive,
    HubNotFound,
    ParcelAlreadyReceived,
    ParcelIsHeld,
    ParcelNotInThisHub,
    ParcelNotSorted,
    SameCityParcelDoesNotTravelBetweenHubs,
    UnknownGovernorate,
)
from hub.domain.messaging import OutboxRecord, OutboxStatus
from hub.domain.value_objects import (
    HoldReason,
    LinehaulStatus,
    ParcelDisposition,
    ParcelPresenceStatus,
    RoutingDecision,
    Urgency,
    normalize_governorate,
)
from hub.infrastructure.contracts.envelopes import build_parcel_held_envelope
from hub.ports.repository import HubUnitOfWork

#: v6.3 p.24, Confirmed decision — a checkpoint interception has one outcome, not a
#: judgement call: "Hudhud returns that parcel to the merchant."
FIXED_DISPOSITIONS: dict[HoldReason, ParcelDisposition] = {
    HoldReason.CHECKPOINT_INTERCEPTION: ParcelDisposition.RETURN_TO_MERCHANT,
    # p.25 — a seal mismatch opens an investigation; it never simply continues.
    HoldReason.TAMPER_INVESTIGATION: ParcelDisposition.AWAIT_OPERATIONS,
}


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class HoldOutcome:
    presence: ParcelPresence
    disposition: ParcelDisposition
    event_id: UUID


class HubProcessingService:
    def __init__(
        self, unit_of_work: HubUnitOfWork, *, outbox_max_attempts: int = 5
    ) -> None:
        self._uow = unit_of_work
        self._outbox_max_attempts = outbox_max_attempts

    # ------------------------------------------------------------- scan in

    def scan_in(
        self, *, hub_id: UUID, tracking_code: str, destination_governorate: str
    ) -> ParcelPresence:
        """"It is scanned in on arrival." (v6.3 p.22)"""
        self._uow.begin()
        try:
            self._require_active_hub(hub_id)
            if self._uow.presences.find_in_hub(hub_id, tracking_code) is not None:
                raise ParcelAlreadyReceived(tracking_code)
            presence = ParcelPresence(
                presence_id=uuid4(),
                hub_id=hub_id,
                tracking_code=tracking_code,
                status=ParcelPresenceStatus.RECEIVED,
                destination_governorate=self._normalize(destination_governorate),
                received_at=_now(),
            )
            self._uow.presences.save(presence)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return presence

    # ------------------------------------------------------------- sort

    def sort(
        self,
        *,
        hub_id: UUID,
        tracking_code: str,
        urgency: Urgency = Urgency.STANDARD,
        route_code: str | None = None,
    ) -> ParcelPresence:
        """Sort by destination city, urgency and route, and decide how it travels.

        The routing decision is derived from the hub's own governorate against the
        parcel's destination, so the same-city rule cannot be forgotten at a counter.
        """
        self._uow.begin()
        try:
            facility = self._require_active_hub(hub_id)
            presence = self._require_present(hub_id, tracking_code)
            self._refuse_if_held(presence)

            presence.urgency = urgency
            presence.route_code = (route_code or "").strip() or None
            presence.routing_decision = (
                RoutingDecision.SAME_CITY_DIRECT
                if presence.destination_governorate == facility.governorate
                else RoutingDecision.INTER_CITY_LINEHAUL
            )
            # A same-city parcel skips the hub-to-hub stage entirely and goes straight
            # toward last-mile delivery (v6.3 p.22).
            presence.status = (
                ParcelPresenceStatus.READY_FOR_LAST_MILE
                if presence.is_same_city
                else ParcelPresenceStatus.SORTED
            )
            presence.sorted_at = _now()
            presence.version += 1
            self._uow.presences.save(presence)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return presence

    # ------------------------------------------------------------- hold

    def hold(
        self,
        *,
        hub_id: UUID,
        tracking_code: str,
        reason: HoldReason,
        reported_by_actor_id: UUID | None = None,
        consignment_id: UUID | None = None,
    ) -> HoldOutcome:
        """Stop a parcel and publish what must happen to it.

        Two reasons have a disposition v6.3 already fixed, so this service applies it
        rather than leaving each consumer to decide.
        """
        self._uow.begin()
        try:
            presence = self._require_present(hub_id, tracking_code)
            disposition = FIXED_DISPOSITIONS.get(
                reason, ParcelDisposition.AWAIT_OPERATIONS
            )
            moment = _now()
            presence.status = ParcelPresenceStatus.HELD
            presence.hold_reason = reason
            presence.disposition = disposition
            if consignment_id is not None:
                presence.consignment_id = consignment_id
            presence.version += 1
            self._uow.presences.save(presence)

            event_id = uuid4()
            payload_json, subject = build_parcel_held_envelope(
                presence=presence,
                aggregate_version=presence.version,
                held_at=moment,
                event_id=event_id,
                correlation_id=uuid4(),
                reported_by_actor_id=reported_by_actor_id,
            )
            self._uow.outbox.insert(
                OutboxRecord(
                    id=uuid4(),
                    event_id=event_id,
                    subject=subject,
                    event_type=payload_json["event_type"],
                    event_version=int(payload_json["event_version"]),
                    aggregate_id=presence.presence_id,
                    aggregate_version=presence.version,
                    payload_json=payload_json,
                    status=OutboxStatus.PENDING,
                    attempt_count=0,
                    max_attempts=self._outbox_max_attempts,
                    next_attempt_at=moment,
                    created_at=moment,
                )
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return HoldOutcome(
            presence=presence, disposition=disposition, event_id=event_id
        )

    def report_checkpoint_interception(
        self, *, hub_id: UUID, tracking_code: str, reported_by_actor_id: UUID
    ) -> HoldOutcome:
        """SHP-13 — a checkpoint opened the parcel, so it goes back to the merchant."""
        return self.hold(
            hub_id=hub_id,
            tracking_code=tracking_code,
            reason=HoldReason.CHECKPOINT_INTERCEPTION,
            reported_by_actor_id=reported_by_actor_id,
        )

    # ------------------------------------------------------------- handover

    def hand_to_last_mile(
        self, *, hub_id: UUID, tracking_code: str
    ) -> ParcelPresence:
        """Release a parcel to a last-mile manifest.

        The custody transfer itself is the driver's scan, which Delivery owns (v6.3 p.26).
        This records only that the hub let the parcel go.
        """
        self._uow.begin()
        try:
            presence = self._require_present(hub_id, tracking_code)
            self._refuse_if_held(presence)
            if presence.status is not ParcelPresenceStatus.READY_FOR_LAST_MILE:
                raise ParcelNotSorted(tracking_code)
            presence.status = ParcelPresenceStatus.HANDED_TO_LAST_MILE
            presence.handed_to_last_mile_at = _now()
            presence.version += 1
            self._uow.presences.save(presence)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return presence

    def assert_travels_between_hubs(self, *, hub_id: UUID, tracking_code: str) -> None:
        """Guard the hub-to-hub stage against a same-city parcel (SHP-06)."""
        self._uow.begin()
        try:
            presence = self._require_present(hub_id, tracking_code)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        if presence.is_same_city:
            raise SameCityParcelDoesNotTravelBetweenHubs(tracking_code)

    # ------------------------------------------------------------- activity

    def activity(self, *, hub_id: UUID, moment: datetime | None = None) -> HubActivitySnapshot:
        """OPS-05 — backlog, ready, delayed and held, in one read."""
        when = moment or _now()
        self._uow.begin()
        try:
            presences = self._uow.presences.list_for_hub(hub_id)
            linehauls = self._uow.linehauls.list_for_hub(hub_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        def count(status: ParcelPresenceStatus) -> int:
            return sum(1 for item in presences if item.status is status)

        delayed = sum(
            1
            for leg in linehauls
            if leg.status is LinehaulStatus.DEPARTED
            and (leg.delay_minutes(when) or 0) > 0
        )
        return HubActivitySnapshot(
            hub_id=hub_id,
            received=count(ParcelPresenceStatus.RECEIVED),
            sorted_count=count(ParcelPresenceStatus.SORTED),
            grouped=count(ParcelPresenceStatus.GROUPED),
            ready_for_last_mile=count(ParcelPresenceStatus.READY_FOR_LAST_MILE),
            held=count(ParcelPresenceStatus.HELD),
            awaiting_linehaul=sum(1 for item in presences if item.awaiting_linehaul),
            delayed_linehauls=delayed,
        )

    def list_parcels(self, *, hub_id: UUID) -> tuple[ParcelPresence, ...]:
        self._uow.begin()
        try:
            found = self._uow.presences.list_for_hub(hub_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _require_present(self, hub_id: UUID, tracking_code: str) -> ParcelPresence:
        presence = self._uow.presences.find_in_hub(hub_id, tracking_code)
        if presence is None:
            raise ParcelNotInThisHub(tracking_code)
        return presence

    @staticmethod
    def _refuse_if_held(presence: ParcelPresence) -> None:
        if presence.is_held:
            reason = presence.hold_reason.value if presence.hold_reason else "unknown"
            raise ParcelIsHeld(presence.tracking_code, reason)

    def _require_active_hub(self, hub_id: UUID) -> Hub:
        facility = self._uow.hubs.get(hub_id)
        if facility is None:
            raise HubNotFound(str(hub_id))
        if not facility.is_active:
            raise HubNotActive(str(hub_id))
        return facility

    @staticmethod
    def _normalize(governorate: str) -> str:
        try:
            return normalize_governorate(governorate)
        except ValueError as exc:
            raise UnknownGovernorate(governorate) from exc
