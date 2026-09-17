"""The customer drop-off path (CUS-03 … CUS-07).

This is the **second way custody can begin**, and v6.3 p.18 describes it precisely:

* a regular customer's parcel never gets a pickup — they bring it in themselves;
* they either entered the details in the app beforehand, or arrive with nothing and hub
  staff take the details on the spot;
* **hub staff — not the customer — stick a label onto the parcel and scan it**;
* from that point the parcel is treated exactly as an accepted parcel would be, and
  custody begins.

The service publishes acceptance as a fact for Shipment to consume, exactly as Pickup
publishes its own acceptance. Hub never writes Shipment's lifecycle itself (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from hub.domain.entities import DropOff, Hub
from hub.domain.errors import (
    CustomerMayNotLabelTheirOwnParcel,
    DropOffDetailsRequired,
    DropOffExpired,
    DropOffNotFound,
    DropOffNotLabelled,
    DropOffTransitionNotAllowed,
    HubNotActive,
    HubNotFound,
    InvalidLabelCode,
    WeightRequiredAtDropOff,
)
from hub.domain.messaging import OutboxRecord, OutboxStatus
from hub.domain.value_objects import DropOffStatus, normalize_label_code
from hub.infrastructure.contracts.envelopes import build_drop_off_accepted_envelope
from hub.ports.repository import HubUnitOfWork

#: CUS-06 — Customer App v3 `created`: "Unclaimed orders are cancelled after 3 days."
DEFAULT_HOLD_DAYS = 3

#: The minimum a hub needs before it can label and route a walk-in parcel. Deliberately
#: the same minimum v6.3 p.12 sets for any shipment: phone and governorate.
REQUIRED_WALK_IN_DETAILS: tuple[str, ...] = ("receiver_phone", "destination_governorate")


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class DropOffPolicy:
    """How long an unclaimed drop-off waits before it lapses."""

    hold_days: int = DEFAULT_HOLD_DAYS


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    drop_off: DropOff
    event_id: UUID


class DropOffService:
    def __init__(
        self,
        unit_of_work: HubUnitOfWork,
        *,
        policy: DropOffPolicy | None = None,
        outbox_max_attempts: int = 5,
    ) -> None:
        self._uow = unit_of_work
        self._policy = policy or DropOffPolicy()
        self._outbox_max_attempts = outbox_max_attempts

    # ------------------------------------------------------------- intake

    def expect_drop_off(
        self,
        *,
        hub_id: UUID,
        tracking_code: str,
        shipment_request_id: UUID | None = None,
        sender_principal_id: UUID | None = None,
    ) -> DropOff:
        """The customer entered details in the app and is bringing the parcel in."""
        self._uow.begin()
        try:
            self._require_active_hub(hub_id)
            moment = _now()
            drop_off = DropOff(
                drop_off_id=uuid4(),
                hub_id=hub_id,
                tracking_code=tracking_code,
                status=DropOffStatus.EXPECTED,
                shipment_request_id=shipment_request_id,
                sender_principal_id=sender_principal_id,
                created_at=moment,
                expires_at=moment + timedelta(days=self._policy.hold_days),
            )
            self._uow.drop_offs.save(drop_off)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return drop_off

    def capture_details(
        self,
        *,
        hub_id: UUID,
        tracking_code: str,
        details: dict[str, str],
        drop_off_id: UUID | None = None,
        sender_principal_id: UUID | None = None,
    ) -> DropOff:
        """Hub staff take the details at the counter.

        Covers both of v6.3's entry points: filling in a walk-in with nothing entered, and
        confirming details a customer submitted in advance.
        """
        missing = tuple(
            name
            for name in REQUIRED_WALK_IN_DETAILS
            if not str(details.get(name, "")).strip()
        )
        if missing:
            raise DropOffDetailsRequired(missing)

        self._uow.begin()
        try:
            self._require_active_hub(hub_id)
            existing = (
                self._uow.drop_offs.get(drop_off_id)
                if drop_off_id is not None
                else self._uow.drop_offs.find_by_tracking_code(tracking_code)
            )
            moment = _now()
            if existing is None:
                drop_off = DropOff(
                    drop_off_id=uuid4(),
                    hub_id=hub_id,
                    tracking_code=tracking_code,
                    status=DropOffStatus.DETAILS_CAPTURED,
                    sender_principal_id=sender_principal_id,
                    captured_details=dict(details),
                    created_at=moment,
                    expires_at=moment + timedelta(days=self._policy.hold_days),
                )
            else:
                drop_off = self._assert_live(existing, moment)
                if not drop_off.can_transition_to(DropOffStatus.DETAILS_CAPTURED):
                    raise DropOffTransitionNotAllowed(
                        drop_off.status.value, DropOffStatus.DETAILS_CAPTURED.value
                    )
                drop_off.status = DropOffStatus.DETAILS_CAPTURED
                drop_off.captured_details = {**drop_off.captured_details, **details}
                drop_off.version += 1
            self._uow.drop_offs.save(drop_off)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return drop_off

    # ------------------------------------------------------------- labelling

    def apply_label(
        self,
        *,
        drop_off_id: UUID,
        label_code: str,
        weight_grams: int,
        operator_principal_id: UUID,
        actor_is_hub_staff: bool,
    ) -> DropOff:
        """Hub staff weigh the parcel, stick a label on it and scan it (CUS-05, CUS-07).

        ``actor_is_hub_staff`` is passed in rather than inferred so the rule is enforced
        in the domain and not only by whichever route happened to call it.
        """
        if not actor_is_hub_staff:
            raise CustomerMayNotLabelTheirOwnParcel()
        if weight_grams <= 0:
            raise WeightRequiredAtDropOff()
        try:
            code = normalize_label_code(label_code)
        except ValueError as exc:
            raise InvalidLabelCode(label_code) from exc

        self._uow.begin()
        try:
            drop_off = self._assert_live(self._load(drop_off_id), _now())
            if not drop_off.can_transition_to(DropOffStatus.LABELLED):
                raise DropOffTransitionNotAllowed(
                    drop_off.status.value, DropOffStatus.LABELLED.value
                )
            drop_off.label_code = code
            drop_off.weight_grams = weight_grams
            drop_off.labelled_by_actor_id = operator_principal_id
            drop_off.status = DropOffStatus.LABELLED
            drop_off.version += 1
            self._uow.drop_offs.save(drop_off)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return drop_off

    # ------------------------------------------------------------- acceptance

    def accept(
        self,
        *,
        drop_off_id: UUID,
        operator_principal_id: UUID,
        destination_governorate: str | None = None,
    ) -> AcceptanceResult:
        """Custody begins. The one custody event on this path, recorded exactly once."""
        self._uow.begin()
        try:
            drop_off = self._load(drop_off_id)
            if drop_off.status is DropOffStatus.ACCEPTED:
                # Re-accepting is a replay, not a second custody event.
                raise DropOffTransitionNotAllowed(
                    drop_off.status.value, DropOffStatus.ACCEPTED.value
                )
            if not drop_off.has_label:
                raise DropOffNotLabelled()
            if not drop_off.can_transition_to(DropOffStatus.ACCEPTED):
                raise DropOffTransitionNotAllowed(
                    drop_off.status.value, DropOffStatus.ACCEPTED.value
                )
            moment = _now()
            drop_off.status = DropOffStatus.ACCEPTED
            drop_off.accepted_at = moment
            drop_off.accepted_by_actor_id = operator_principal_id
            drop_off.version += 1
            self._uow.drop_offs.save(drop_off)

            event_id = uuid4()
            payload_json, subject = build_drop_off_accepted_envelope(
                drop_off=drop_off,
                aggregate_version=drop_off.version,
                event_id=event_id,
                correlation_id=uuid4(),
                destination_governorate=destination_governorate,
            )
            self._uow.outbox.insert(
                OutboxRecord(
                    id=uuid4(),
                    event_id=event_id,
                    subject=subject,
                    event_type=payload_json["event_type"],
                    event_version=int(payload_json["event_version"]),
                    aggregate_id=drop_off.drop_off_id,
                    aggregate_version=drop_off.version,
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
        return AcceptanceResult(drop_off=drop_off, event_id=event_id)

    # ------------------------------------------------------------- lapse

    def expire_unclaimed(self, *, hub_id: UUID, moment: datetime | None = None) -> int:
        """CUS-06 — cancel drop-offs nobody brought in within the hold window."""
        when = moment or _now()
        self._uow.begin()
        try:
            expired = 0
            for drop_off in self._uow.drop_offs.list_open_for_hub(hub_id):
                if not drop_off.is_expired_at(when):
                    continue
                drop_off.status = DropOffStatus.EXPIRED
                drop_off.closed_at = when
                drop_off.version += 1
                self._uow.drop_offs.save(drop_off)
                expired += 1
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return expired

    def cancel(self, *, drop_off_id: UUID) -> DropOff:
        self._uow.begin()
        try:
            drop_off = self._load(drop_off_id)
            if not drop_off.can_transition_to(DropOffStatus.CANCELLED):
                raise DropOffTransitionNotAllowed(
                    drop_off.status.value, DropOffStatus.CANCELLED.value
                )
            drop_off.status = DropOffStatus.CANCELLED
            drop_off.closed_at = _now()
            drop_off.version += 1
            self._uow.drop_offs.save(drop_off)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return drop_off

    # ------------------------------------------------------------- queries

    def get(self, drop_off_id: UUID) -> DropOff:
        self._uow.begin()
        try:
            drop_off = self._load(drop_off_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return drop_off

    def list_open(self, *, hub_id: UUID) -> tuple[DropOff, ...]:
        self._uow.begin()
        try:
            found = self._uow.drop_offs.list_open_for_hub(hub_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _load(self, drop_off_id: UUID) -> DropOff:
        drop_off = self._uow.drop_offs.get(drop_off_id)
        if drop_off is None:
            raise DropOffNotFound(str(drop_off_id))
        return drop_off

    def _assert_live(self, drop_off: DropOff, moment: datetime) -> DropOff:
        """A lapsed drop-off is refused rather than quietly revived."""
        if drop_off.is_expired_at(moment):
            raise DropOffExpired(drop_off.tracking_code)
        return drop_off

    def _require_active_hub(self, hub_id: UUID) -> Hub:
        facility = self._uow.hubs.get(hub_id)
        if facility is None:
            raise HubNotFound(str(hub_id))
        if not facility.is_active:
            raise HubNotActive(str(hub_id))
        return facility
