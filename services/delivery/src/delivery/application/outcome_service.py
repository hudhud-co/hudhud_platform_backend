"""How a door visit ends: delivered, refused, or a failed attempt.

Three v6.3 rules meet here.

* **A COD parcel is not Delivered unless payment was actually collected** (p.31). The
  handover asks the payment service and refuses without it.
* **A refusal and a failed attempt both leave the parcel in HUDHUD custody** (p.28, p.34),
  and nothing is collected in either case.
* **Operations decides the next attempt** (OPS-08). The driver records what happened; the
  disposition — retry, hold, or return to the merchant — is somebody else's call, and the
  three-day hold of p.29 is the policy behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from delivery.application.doorstep_service import check_ready_for_payment
from delivery.application.payment_service import check_payment_satisfies
from delivery.domain.entities import DeliveryStop, FailedAttempt
from delivery.domain.errors import (
    FailedAttemptNotFound,
    NextAttemptAlreadyDecided,
    NotThisDriversStop,
    NotVerified,
    OnlyOperationsDecidesTheNextAttempt,
    StopNotFound,
    StopTransitionNotAllowed,
)
from delivery.domain.messaging import OutboxRecord, OutboxStatus
from delivery.domain.value_objects import (
    FailureReason,
    NextAttemptDecision,
    PaymentMethod,
    RefusalReason,
    StopStatus,
)
from delivery.infrastructure.contracts.envelopes import (
    build_attempt_failed_envelope,
    build_cod_collected_envelope,
    build_delivered_envelope,
    build_departed_envelope,
)
from delivery.ports.repository import DeliveryUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class HandoverResult:
    stop: DeliveryStop
    delivered_event_id: UUID
    #: Present only when money changed hands at the door.
    cod_event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class FailureResult:
    stop: DeliveryStop
    attempt: FailedAttempt
    event_id: UUID


class OutcomeService:
    def __init__(
        self,
        unit_of_work: DeliveryUnitOfWork,
        *,
        payment_service,
        doorstep_service,
        outbox_max_attempts: int = 5,
    ) -> None:
        self._uow = unit_of_work
        self._payments = payment_service
        self._doorstep = doorstep_service
        self._outbox_max_attempts = outbox_max_attempts

    # ------------------------------------------------------------- set off

    def announce_departure(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        eta_from_minutes: int | None = None,
        eta_to_minutes: int | None = None,
    ) -> UUID:
        """DRV-L01, NTF-07 — the receiver is told before the driver sets off.

        The notice is an ETA, never an exact time, and the three channels are
        Notification's to fan out.
        """
        # The transition and its event share one transaction; the departure notice
        # must not be able to exist without the departure, or the other way round.
        self._uow.begin()
        try:
            stop = self._reload(stop_id)
            if stop.driver_principal_id != driver_principal_id:
                raise NotThisDriversStop(stop.tracking_code)
            if not stop.can_transition_to(StopStatus.EN_ROUTE):
                raise StopTransitionNotAllowed(
                    stop.status.value, StopStatus.EN_ROUTE.value
                )
            moment = _now()
            stop.status = StopStatus.EN_ROUTE
            stop.departed_at = moment
            stop.version += 1
            self._uow.stops.save(stop)

            event_id = uuid4()
            payload_json, subject = build_departed_envelope(
                stop=stop,
                aggregate_version=stop.version,
                event_id=event_id,
                correlation_id=uuid4(),
                eta_from_minutes=eta_from_minutes,
                eta_to_minutes=eta_to_minutes,
            )
            self._enqueue(payload_json, subject, stop.stop_id, stop.version)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return event_id

    # ------------------------------------------------------------- handover

    def complete_delivery(
        self, *, stop_id: UUID, driver_principal_id: UUID
    ) -> HandoverResult:
        """The handover. Custody passes to the receiver here and nowhere else."""
        self._uow.begin()
        try:
            stop = self._reload(stop_id)
            if stop.driver_principal_id != driver_principal_id:
                raise NotThisDriversStop(stop.tracking_code)
            if not stop.is_verified:
                raise NotVerified()
            photos = self._uow.photos.list_for_stop(stop.stop_id)
            check_ready_for_payment(stop=stop, photos=photos)
            # v6.3 p.31 — refuses when a COD parcel has not been paid. Read and checked
            # inside this transaction so the money cannot change between the two.
            payment = check_payment_satisfies(
                stop=stop, payment=self._uow.payments.find_for_stop(stop.stop_id)
            )
            if not stop.can_transition_to(StopStatus.DELIVERED):
                raise StopTransitionNotAllowed(
                    stop.status.value, StopStatus.DELIVERED.value
                )
            moment = _now()
            stop.status = StopStatus.DELIVERED
            stop.delivered_at = moment
            stop.closed_at = moment
            stop.version += 1
            self._uow.stops.save(stop)

            delivered_event_id = uuid4()
            payload_json, subject = build_delivered_envelope(
                stop=stop,
                payment=payment,
                photo_count=len(photos),
                aggregate_version=stop.version,
                event_id=delivered_event_id,
                correlation_id=uuid4(),
            )
            self._enqueue(payload_json, subject, stop.stop_id, stop.version)

            cod_event_id: UUID | None = None
            if payment is not None and payment.method is not PaymentMethod.PREPAID:
                # A second fact on the same aggregate needs its own version, or the
                # ordering key consumers rely on would collide.
                stop.version += 1
                self._uow.stops.save(stop)
                cod_event_id = uuid4()
                cod_payload, cod_subject = build_cod_collected_envelope(
                    stop=stop,
                    payment=payment,
                    aggregate_version=stop.version,
                    event_id=cod_event_id,
                    correlation_id=uuid4(),
                )
                self._enqueue(cod_payload, cod_subject, stop.stop_id, stop.version)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return HandoverResult(
            stop=stop, delivered_event_id=delivered_event_id, cod_event_id=cod_event_id
        )

    # ------------------------------------------------------------- refusal

    def record_refusal(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        reason: RefusalReason | None = None,
    ) -> FailureResult:
        """v6.3 p.34 — nothing is collected, and the parcel stays in HUDHUD custody.

        The reason is optional: Driver App v8 `lmRefuse` says so in as many words.
        """
        return self._close_without_handover(
            stop_id=stop_id,
            driver_principal_id=driver_principal_id,
            status=StopStatus.REFUSED,
            failure_reason=FailureReason.RECEIVER_REFUSED,
            refusal_reason=reason,
        )

    def record_absence(
        self, *, stop_id: UUID, driver_principal_id: UUID, moment: datetime | None = None
    ) -> FailureResult:
        """v6.3 p.26 — recordable only once the ten minutes are up."""
        when = moment or _now()
        stop = self._load(stop_id, driver_principal_id)
        self._doorstep.assert_wait_is_over(stop=stop, moment=when)
        return self._close_without_handover(
            stop_id=stop_id,
            driver_principal_id=driver_principal_id,
            status=StopStatus.FAILED,
            failure_reason=FailureReason.ABSENT_AFTER_WAIT,
        )

    def record_verification_failure(
        self, *, stop_id: UUID, driver_principal_id: UUID
    ) -> FailureResult:
        """DRV-L08 — verification could not be completed; the parcel stays with HUDHUD."""
        return self._close_without_handover(
            stop_id=stop_id,
            driver_principal_id=driver_principal_id,
            status=StopStatus.FAILED,
            failure_reason=FailureReason.VERIFICATION_NOT_COMPLETED,
        )

    # ------------------------------------------------------------- OPS-08

    def decide_next_attempt(
        self,
        *,
        attempt_id: UUID,
        decision: NextAttemptDecision,
        decider_principal_id: UUID,
        actor_is_operations: bool,
    ) -> FailedAttempt:
        """OPS-08 — "Held for next attempt — decided by operations".

        ``actor_is_operations`` is passed in rather than inferred, so the rule holds in
        the domain and not only in whichever route happened to call it.
        """
        if not actor_is_operations:
            raise OnlyOperationsDecidesTheNextAttempt()

        self._uow.begin()
        try:
            attempt = self._uow.failed_attempts.get(attempt_id)
            if attempt is None:
                raise FailedAttemptNotFound(str(attempt_id))
            if attempt.next_attempt_decision is not None:
                raise NextAttemptAlreadyDecided(attempt.next_attempt_decision.value)
            attempt.next_attempt_decision = decision
            attempt.decided_at = _now()
            attempt.decided_by_actor_id = decider_principal_id
            attempt.version += 1
            self._uow.failed_attempts.save(attempt)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return attempt

    def attempts_past_their_hold(
        self, *, moment: datetime | None = None
    ) -> tuple[FailedAttempt, ...]:
        """DRV-L20 — parcels the three-day hold has run out on (v6.3 p.29)."""
        when = moment or _now()
        self._uow.begin()
        try:
            found = tuple(
                attempt
                for attempt in self._uow.failed_attempts.list_awaiting_operations()
                if attempt.must_return_to_the_merchant(when)
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def return_parcels_past_their_hold(
        self, *, actor_principal_id: UUID, moment: datetime | None = None
    ) -> tuple[FailedAttempt, ...]:
        """v6.3 p.29 — "still undelivered after the hold ⇒ returned to the merchant".

        The three-attempt limit this replaced could be reached at any time; a hold can
        only be reached by the clock, so something has to come and read it. That is this.

        The returning actor is recorded rather than left as a null "system": the check
        constraint requires every disposition to name who made it, and an unattributed
        return is exactly what OPS-08 exists to prevent.
        """
        when = moment or _now()
        returned: list[FailedAttempt] = []
        self._uow.begin()
        try:
            for attempt in self._uow.failed_attempts.list_awaiting_operations():
                if not attempt.must_return_to_the_merchant(when):
                    continue
                attempt.next_attempt_decision = NextAttemptDecision.RETURN_TO_MERCHANT
                attempt.decided_at = when
                attempt.decided_by_actor_id = actor_principal_id
                attempt.version += 1
                self._uow.failed_attempts.save(attempt)
                returned.append(attempt)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(returned)

    def attempts_awaiting_operations(self) -> tuple[FailedAttempt, ...]:
        self._uow.begin()
        try:
            found = self._uow.failed_attempts.list_awaiting_operations()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _close_without_handover(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        status: StopStatus,
        failure_reason: FailureReason,
        refusal_reason: RefusalReason | None = None,
    ) -> FailureResult:
        self._uow.begin()
        try:
            stop = self._reload(stop_id)
            if stop.driver_principal_id != driver_principal_id:
                raise NotThisDriversStop(stop.tracking_code)
            if not stop.can_transition_to(status):
                raise StopTransitionNotAllowed(stop.status.value, status.value)

            moment = _now()
            stop.status = status
            stop.failure_reason = failure_reason
            stop.refusal_reason = refusal_reason
            # Any inspection outcome already recorded stands: ``record_inspection``
            # writes OPEN_BOX_REFUSED when the receiver refuses after opening, and a
            # sealed refusal at the door never had an inspection to record.
            stop.closed_at = moment
            stop.version += 1
            self._uow.stops.save(stop)

            attempt = FailedAttempt(
                attempt_id=uuid4(),
                stop_id=stop.stop_id,
                tracking_code=stop.tracking_code,
                reason=failure_reason,
                recorded_at=moment,
                recorded_by_actor_id=driver_principal_id,
            )
            self._uow.failed_attempts.save(attempt)

            event_id = uuid4()
            payload_json, subject = build_attempt_failed_envelope(
                stop=stop,
                attempt=attempt,
                aggregate_version=stop.version,
                event_id=event_id,
                correlation_id=uuid4(),
            )
            self._enqueue(payload_json, subject, stop.stop_id, stop.version)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return FailureResult(stop=stop, attempt=attempt, event_id=event_id)

    def _enqueue(
        self, payload_json: dict, subject: str, aggregate_id: UUID, aggregate_version: int
    ) -> None:
        moment = _now()
        self._uow.outbox.insert(
            OutboxRecord(
                id=uuid4(),
                event_id=UUID(payload_json["event_id"]),
                subject=subject,
                event_type=payload_json["event_type"],
                event_version=int(payload_json["event_version"]),
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                payload_json=payload_json,
                status=OutboxStatus.PENDING,
                attempt_count=0,
                max_attempts=self._outbox_max_attempts,
                next_attempt_at=moment,
                created_at=moment,
            )
        )

    def _load(self, stop_id: UUID, driver_principal_id: UUID) -> DeliveryStop:
        stop = self._doorstep.get_stop(stop_id)
        if stop.driver_principal_id != driver_principal_id:
            raise NotThisDriversStop(stop.tracking_code)
        return stop

    def _reload(self, stop_id: UUID) -> DeliveryStop:
        stop = self._uow.stops.get(stop_id)
        if stop is None:
            raise StopNotFound(str(stop_id))
        return stop
