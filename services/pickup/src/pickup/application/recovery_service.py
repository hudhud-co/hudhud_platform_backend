"""Pickup recovery lifecycle application service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pickup.domain.entities import IdempotencyRecord, PickupTask, RecoveryHistoryEntry
from pickup.domain.errors import (
    ConflictingIdempotencyKey,
    CustodyAlreadyStarted,
    InvalidRescheduleInput,
    MissingReassignmentDriver,
    PickupTaskAlreadyAccepted,
    PickupTaskNotFound,
    PickupTaskNotRecoverable,
    StalePickupTaskVersion,
)
from pickup.domain.value_objects import (
    CustodyType,
    PickupTaskStatus,
    RecoveryAction,
    ScheduledWindow,
)
from pickup.ports.repository import RecoveryUnitOfWork
from pickup.ports.shipment_eligibility import ShipmentEligibilityPort


@dataclass(frozen=True, slots=True)
class RegisterPickupTaskCommand:
    pickup_task_id: UUID
    shipment_id: UUID
    assigned_driver_user_id: str
    assigned_batch_id: UUID
    scheduled_window: ScheduledWindow | None = None
    status: PickupTaskStatus = PickupTaskStatus.PROOF_CAPTURED
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecoveryCommand:
    pickup_task_id: UUID
    idempotency_key: str
    reason: str | None
    occurred_at: datetime
    expected_version: int | None = None


@dataclass(frozen=True, slots=True)
class RescheduleRecoveryCommand(RecoveryCommand):
    scheduled_window: ScheduledWindow | None = None


@dataclass(frozen=True, slots=True)
class ReassignRecoveryCommand(RecoveryCommand):
    new_driver_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    original_task: PickupTask
    replacement_task: PickupTask | None
    history_entry: RecoveryHistoryEntry
    idempotent_replay: bool = False


class PickupRecoveryService:
    """PickupTask recovery lifecycle — retry, reschedule, reassign, cancel (W12)."""

    def __init__(
        self,
        unit_of_work: RecoveryUnitOfWork,
        shipment_eligibility: ShipmentEligibilityPort,
    ) -> None:
        self._uow = unit_of_work
        self._shipment_eligibility = shipment_eligibility

    def register_pickup_task(self, command: RegisterPickupTaskCommand) -> PickupTask:
        """Persist an initial pickup attempt for recovery tests and bootstrap seeding."""
        created_at = command.created_at or datetime.now(tz=UTC)
        window_start = command.scheduled_window.start if command.scheduled_window else None
        window_end = command.scheduled_window.end if command.scheduled_window else None
        task = PickupTask(
            pickup_task_id=command.pickup_task_id,
            shipment_id=command.shipment_id,
            assigned_driver_user_id=command.assigned_driver_user_id,
            assigned_batch_id=command.assigned_batch_id,
            status=command.status,
            attempt_number=1,
            root_attempt_id=command.pickup_task_id,
            parent_attempt_id=None,
            superseded_by_task_id=None,
            scheduled_window_start=window_start,
            scheduled_window_end=window_end,
            acceptance_state=None,
            recovery_reason=None,
            created_at=created_at,
            recovered_at=None,
            cancelled_at=None,
            version=1,
        )
        self._uow.pickup_tasks.save_pickup_task(task)
        return task

    def retry_pickup(self, command: RecoveryCommand) -> RecoveryResult:
        return self._execute_recovery(command, RecoveryAction.RETRY)

    def reschedule_pickup(self, command: RescheduleRecoveryCommand) -> RecoveryResult:
        if command.scheduled_window is None:
            raise InvalidRescheduleInput("reschedule requires scheduled_window")
        if command.scheduled_window.end <= command.scheduled_window.start:
            raise InvalidRescheduleInput("scheduled window end must be after start")
        return self._execute_recovery(
            command,
            RecoveryAction.RESCHEDULE,
            scheduled_window=command.scheduled_window,
        )

    def reassign_pickup(self, command: ReassignRecoveryCommand) -> RecoveryResult:
        if not command.new_driver_user_id:
            raise MissingReassignmentDriver()
        return self._execute_recovery(
            command,
            RecoveryAction.REASSIGN,
            new_driver_user_id=command.new_driver_user_id,
        )

    def cancel_pickup(self, command: RecoveryCommand) -> RecoveryResult:
        return self._execute_recovery(command, RecoveryAction.CANCEL)

    def _execute_recovery(
        self,
        command: RecoveryCommand,
        action: RecoveryAction,
        *,
        scheduled_window: ScheduledWindow | None = None,
        new_driver_user_id: str | None = None,
    ) -> RecoveryResult:
        fingerprint = _command_fingerprint(
            action=action,
            pickup_task_id=command.pickup_task_id,
            scheduled_window=scheduled_window,
            new_driver_user_id=new_driver_user_id,
        )
        cached = self._uow.idempotency.get_record(command.idempotency_key)
        if cached is not None:
            if cached.command_fingerprint != fingerprint:
                raise ConflictingIdempotencyKey(idempotency_key=command.idempotency_key)
            return self._reconstruct_cached_result(cached)

        self._uow.begin()
        try:
            result = self._apply_recovery(
                command=command,
                action=action,
                fingerprint=fingerprint,
                scheduled_window=scheduled_window,
                new_driver_user_id=new_driver_user_id,
            )
        except Exception:
            self._uow.rollback()
            raise
        else:
            self._uow.commit()
            return result

    def _apply_recovery(
        self,
        *,
        command: RecoveryCommand,
        action: RecoveryAction,
        fingerprint: str,
        scheduled_window: ScheduledWindow | None,
        new_driver_user_id: str | None,
    ) -> RecoveryResult:
        cached = self._uow.idempotency.get_record(command.idempotency_key)
        if cached is not None:
            if cached.command_fingerprint != fingerprint:
                raise ConflictingIdempotencyKey(idempotency_key=command.idempotency_key)
            return self._reconstruct_cached_result(cached)

        task = self._uow.pickup_tasks.get_pickup_task(command.pickup_task_id)
        if task is None:
            raise PickupTaskNotFound(str(command.pickup_task_id))

        if command.expected_version is not None and task.version != command.expected_version:
            raise StalePickupTaskVersion(
                pickup_task_id=str(task.pickup_task_id),
                expected_version=command.expected_version,
                actual_version=task.version,
            )

        self._validate_recovery_eligibility(task)

        replacement: PickupTask | None = None
        if action is RecoveryAction.CANCEL:
            task.status = PickupTaskStatus.CANCELLED
            task.cancelled_at = command.occurred_at
            task.recovery_reason = command.reason
            task.version += 1
            self._uow.pickup_tasks.save_pickup_task(task)
            result_task_id: UUID | None = task.pickup_task_id
        else:
            replacement_id = uuid4()
            task.status = PickupTaskStatus.SUPERSEDED
            task.superseded_by_task_id = replacement_id
            task.recovered_at = command.occurred_at
            task.recovery_reason = command.reason
            task.version += 1
            self._uow.pickup_tasks.save_pickup_task(task)

            replacement = self._build_replacement_task(
                original=task,
                replacement_id=replacement_id,
                action=action,
                command=command,
                scheduled_window=scheduled_window,
                new_driver_user_id=new_driver_user_id,
            )
            self._uow.pickup_tasks.save_pickup_task(replacement)
            result_task_id = replacement.pickup_task_id

        history_entry = RecoveryHistoryEntry(
            history_id=uuid4(),
            pickup_task_id=task.pickup_task_id,
            replacement_task_id=replacement.pickup_task_id if replacement else None,
            action=action,
            reason=command.reason,
            idempotency_key=command.idempotency_key,
            occurred_at=command.occurred_at,
        )
        self._uow.recovery_history.append_entry(history_entry)

        idempotency_record = IdempotencyRecord(
            idempotency_key=command.idempotency_key,
            command_fingerprint=fingerprint,
            pickup_task_id=command.pickup_task_id,
            action=action,
            original_task_id=task.pickup_task_id,
            result_task_id=result_task_id,
            recorded_at=command.occurred_at,
        )
        self._uow.idempotency.save_record(idempotency_record)

        return RecoveryResult(
            original_task=task,
            replacement_task=replacement,
            history_entry=history_entry,
        )

    def _validate_recovery_eligibility(self, task: PickupTask) -> None:
        if task.is_accepted:
            assert task.acceptance_state is not None
            raise PickupTaskAlreadyAccepted(
                pickup_task_id=str(task.pickup_task_id),
                acceptance_state=task.acceptance_state.value,
            )
        if task.is_terminal:
            raise PickupTaskNotRecoverable(
                pickup_task_id=str(task.pickup_task_id),
                status=task.status.value,
            )

        # Fail closed when Shipment eligibility evidence is unavailable.
        snapshot = self._shipment_eligibility.get_eligibility(task.shipment_id)
        if snapshot is None:
            raise CustodyAlreadyStarted(
                shipment_id=str(task.shipment_id),
                shipment_status="UNKNOWN",
            )
        # Source-aligned rule (ADR-0003 W17-A / Legacy): block only on
        # PICKUP_DRIVER custody. Do not block on IN_CUSTODY status,
        # custody_started, or custody_id alone.
        if snapshot.custody_type is CustodyType.PICKUP_DRIVER:
            raise CustodyAlreadyStarted(
                shipment_id=str(task.shipment_id),
                shipment_status=snapshot.shipment_status.value,
            )

    def _build_replacement_task(
        self,
        *,
        original: PickupTask,
        replacement_id: UUID,
        action: RecoveryAction,
        command: RecoveryCommand,
        scheduled_window: ScheduledWindow | None,
        new_driver_user_id: str | None,
    ) -> PickupTask:
        driver_id = original.assigned_driver_user_id
        window_start = original.scheduled_window_start
        window_end = original.scheduled_window_end

        if action is RecoveryAction.RESCHEDULE:
            assert scheduled_window is not None
            window_start = scheduled_window.start
            window_end = scheduled_window.end
        elif action is RecoveryAction.REASSIGN:
            assert new_driver_user_id is not None
            driver_id = new_driver_user_id

        return PickupTask(
            pickup_task_id=replacement_id,
            shipment_id=original.shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=original.assigned_batch_id,
            status=PickupTaskStatus.PENDING,
            attempt_number=original.attempt_number + 1,
            root_attempt_id=original.root_attempt_id,
            parent_attempt_id=original.pickup_task_id,
            superseded_by_task_id=None,
            scheduled_window_start=window_start,
            scheduled_window_end=window_end,
            acceptance_state=None,
            recovery_reason=command.reason,
            created_at=command.occurred_at,
            recovered_at=None,
            cancelled_at=None,
            version=1,
        )

    def _reconstruct_cached_result(self, record: IdempotencyRecord) -> RecoveryResult:
        original = self._uow.pickup_tasks.get_pickup_task(record.original_task_id)
        if original is None:
            raise PickupTaskNotFound(str(record.original_task_id))

        replacement: PickupTask | None = None
        if record.result_task_id is not None and record.result_task_id != record.original_task_id:
            replacement = self._uow.pickup_tasks.get_pickup_task(record.result_task_id)

        history = self._uow.recovery_history.list_entries_for_task(record.original_task_id)
        matching = [
            entry
            for entry in history
            if entry.idempotency_key == record.idempotency_key and entry.action is record.action
        ]
        if not matching:
            msg = f"missing recovery history for idempotency key {record.idempotency_key}"
            raise RuntimeError(msg)
        return RecoveryResult(
            original_task=original,
            replacement_task=replacement,
            history_entry=matching[0],
            idempotent_replay=True,
        )


def _command_fingerprint(
    *,
    action: RecoveryAction,
    pickup_task_id: UUID,
    scheduled_window: ScheduledWindow | None,
    new_driver_user_id: str | None,
) -> str:
    payload = {
        "action": action.value,
        "pickup_task_id": str(pickup_task_id),
        "scheduled_window": (
            {
                "start": scheduled_window.start.isoformat(),
                "end": scheduled_window.end.isoformat(),
            }
            if scheduled_window is not None
            else None
        ),
        "new_driver_user_id": new_driver_user_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
