"""Pickup acceptance application service — transactional outbox for pickup.fact.accepted."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pickup.application.accepted_fact_mapper import build_accepted_fact_envelope
from pickup.domain.entities import AcceptanceIdempotencyRecord, OutboxRecord, PickupTask
from pickup.domain.errors import (
    AcceptanceOutcomeNotAllowed,
    ActingDriverMismatch,
    ConflictingIdempotencyKey,
    ExceptionEvidenceRequired,
    PickupConditionProofMissing,
    PickupTaskAlreadyAccepted,
    PickupTaskMissingAssignedBatch,
    PickupTaskMissingAssignedDriver,
    PickupTaskNotAcceptable,
    PickupTaskNotFound,
    PickupTaskNotProofCaptured,
)
from pickup.domain.value_objects import (
    AcceptanceOutcome,
    EvidenceMediaRef,
    OutboxStatus,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
)
from pickup.ports.repository import AcceptanceUnitOfWork

DEFAULT_OUTBOX_MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class AcceptPickupTaskCommand:
    pickup_task_id: UUID
    acting_driver_user_id: str
    scanned_identifier: str
    outcome: AcceptanceOutcome | str
    idempotency_key: str
    accepted_at: datetime
    media_refs: tuple[EvidenceMediaRef, ...] = ()
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    tenant_id: UUID | None = None
    traceparent: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptPickupTaskResult:
    pickup_task: PickupTask
    event_id: UUID
    aggregate_version: int
    outbox_record: OutboxRecord
    idempotent_replay: bool = False


class PickupAcceptanceService:
    """Record custody-starting acceptance and enqueue pickup.fact.accepted atomically."""

    def __init__(self, unit_of_work: AcceptanceUnitOfWork) -> None:
        self._uow = unit_of_work

    def accept_pickup_task(self, command: AcceptPickupTaskCommand) -> AcceptPickupTaskResult:
        if not command.idempotency_key.strip():
            msg = "idempotency key is required"
            raise ValueError(msg)

        outcome = _coerce_outcome(command.outcome)
        fingerprint = _command_fingerprint(command, outcome)
        cached = self._uow.acceptance_idempotency.get_record(command.idempotency_key)
        if cached is not None:
            if cached.command_fingerprint != fingerprint:
                raise ConflictingIdempotencyKey(idempotency_key=command.idempotency_key)
            return self._reconstruct_cached_result(cached)

        self._uow.begin()
        try:
            cached = self._uow.acceptance_idempotency.get_record(command.idempotency_key)
            if cached is not None:
                if cached.command_fingerprint != fingerprint:
                    raise ConflictingIdempotencyKey(idempotency_key=command.idempotency_key)
                result = self._reconstruct_cached_result(cached)
            else:
                result = self._apply_acceptance(command, outcome, fingerprint)
        except Exception:
            self._uow.rollback()
            raise
        else:
            self._uow.commit()
            return result

    def _apply_acceptance(
        self,
        command: AcceptPickupTaskCommand,
        outcome: AcceptanceOutcome,
        fingerprint: str,
    ) -> AcceptPickupTaskResult:
        task = self._uow.pickup_tasks.get_pickup_task(command.pickup_task_id)
        if task is None:
            raise PickupTaskNotFound(str(command.pickup_task_id))

        self._validate_prerequisites(task=task, acting_driver_user_id=command.acting_driver_user_id)

        if outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION and not command.media_refs:
            raise ExceptionEvidenceRequired()

        previous_version = task.version
        next_version = previous_version + 1
        event_id = uuid4()
        correlation_id = command.correlation_id or uuid4()
        accepted_at = command.accepted_at
        if accepted_at.tzinfo is None:
            accepted_at = accepted_at.replace(tzinfo=UTC)

        payload_json, subject = build_accepted_fact_envelope(
            task=task,
            outcome=outcome,
            scanned_identifier=command.scanned_identifier,
            accepted_at=accepted_at,
            aggregate_version=next_version,
            event_id=event_id,
            correlation_id=correlation_id,
            acting_driver_user_id=command.acting_driver_user_id,
            media_refs=command.media_refs,
            causation_id=command.causation_id,
            tenant_id=command.tenant_id,
            traceparent=command.traceparent,
        )

        task.acceptance_state = PickupTaskAcceptanceState(outcome.value)
        task.accepted_at = accepted_at
        task.accepted_by_driver_user_id = command.acting_driver_user_id
        task.version = next_version
        self._uow.pickup_tasks.save_pickup_task(task)

        now = datetime.now(tz=UTC)
        outbox = OutboxRecord(
            id=uuid4(),
            event_id=event_id,
            subject=subject,
            event_type=payload_json["event_type"],
            event_version=int(payload_json["event_version"]),
            aggregate_id=task.pickup_task_id,
            aggregate_version=next_version,
            payload_json=payload_json,
            status=OutboxStatus.PENDING,
            attempt_count=0,
            max_attempts=DEFAULT_OUTBOX_MAX_ATTEMPTS,
            next_attempt_at=now,
            processing_owner=None,
            processing_until=None,
            published_at=None,
            last_error_code=None,
            last_error_message=None,
            created_at=now,
        )
        self._uow.outbox.insert(outbox)
        self._uow.acceptance_idempotency.save_record(
            AcceptanceIdempotencyRecord(
                idempotency_key=command.idempotency_key,
                command_fingerprint=fingerprint,
                pickup_task_id=task.pickup_task_id,
                event_id=event_id,
                recorded_at=accepted_at,
            )
        )
        return AcceptPickupTaskResult(
            pickup_task=task,
            event_id=event_id,
            aggregate_version=next_version,
            outbox_record=outbox,
        )

    def _reconstruct_cached_result(
        self,
        cached: AcceptanceIdempotencyRecord,
    ) -> AcceptPickupTaskResult:
        task = self._uow.pickup_tasks.get_pickup_task(cached.pickup_task_id)
        if task is None:
            raise PickupTaskNotFound(str(cached.pickup_task_id))
        outbox = self._uow.outbox.get_by_event_id(cached.event_id)
        if outbox is None:
            msg = f"missing outbox row for acceptance event_id {cached.event_id}"
            raise RuntimeError(msg)
        return AcceptPickupTaskResult(
            pickup_task=task,
            event_id=cached.event_id,
            aggregate_version=outbox.aggregate_version,
            outbox_record=outbox,
            idempotent_replay=True,
        )

    def _validate_prerequisites(
        self,
        *,
        task: PickupTask,
        acting_driver_user_id: str,
    ) -> None:
        if task.is_accepted:
            assert task.acceptance_state is not None
            raise PickupTaskAlreadyAccepted(
                pickup_task_id=str(task.pickup_task_id),
                acceptance_state=task.acceptance_state.value,
            )
        if task.acceptance_state is PickupTaskAcceptanceState.REJECTED:
            raise PickupTaskAlreadyAccepted(
                pickup_task_id=str(task.pickup_task_id),
                acceptance_state=task.acceptance_state.value,
            )
        if task.is_terminal:
            raise PickupTaskNotAcceptable(
                pickup_task_id=str(task.pickup_task_id),
                status=task.status.value,
            )
        if task.status is not PickupTaskStatus.PROOF_CAPTURED:
            raise PickupTaskNotProofCaptured(
                pickup_task_id=str(task.pickup_task_id),
                current_status=task.status.value,
            )
        if not task.assigned_driver_user_id.strip():
            raise PickupTaskMissingAssignedDriver(pickup_task_id=str(task.pickup_task_id))
        if task.assigned_batch_id is None:
            raise PickupTaskMissingAssignedBatch(pickup_task_id=str(task.pickup_task_id))
        if acting_driver_user_id != task.assigned_driver_user_id:
            raise ActingDriverMismatch(
                pickup_task_id=str(task.pickup_task_id),
                acting_driver_user_id=acting_driver_user_id,
            )
        if not task.has_pickup_condition_proof:
            raise PickupConditionProofMissing(pickup_task_id=str(task.pickup_task_id))


def _coerce_outcome(outcome: AcceptanceOutcome | str) -> AcceptanceOutcome:
    if isinstance(outcome, AcceptanceOutcome):
        return outcome
    try:
        return AcceptanceOutcome(outcome)
    except ValueError as exc:
        raise AcceptanceOutcomeNotAllowed(outcome=str(outcome)) from exc


def _command_fingerprint(command: AcceptPickupTaskCommand, outcome: AcceptanceOutcome) -> str:
    payload = {
        "pickup_task_id": str(command.pickup_task_id),
        "acting_driver_user_id": command.acting_driver_user_id,
        "scanned_identifier": command.scanned_identifier.strip(),
        "outcome": outcome.value,
        "accepted_at": command.accepted_at.isoformat(),
        "media_refs": [
            {
                "ref_type": ref.ref_type,
                "bucket": ref.bucket,
                "key": ref.key,
                "content_type": ref.content_type,
            }
            for ref in command.media_refs
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
