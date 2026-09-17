"""PostgreSQL repositories for driver workforce, handover, and offline aggregates.

These repositories write through the unit-of-work session immediately and flush, so a
command can read its own writes inside the transaction (recount a manifest, resume a
sequence gap) while still committing atomically with everything else.

Row locking is explicit where a race would otherwise be possible: the driver's open work
session, the challenge being verified, and the manifest line being received are all
selected ``FOR UPDATE`` so two concurrent requests serialize instead of both winning.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from pickup.domain.entities import TaskHistoryEntry
from pickup.domain.handover import (
    ACTIVE_HANDOVER_MANIFEST_STATUSES,
    CourierChallenge,
    CourierChallengeStatus,
    CourierManifest,
    CourierManifestStatus,
    HandoverDiscrepancyReason,
    HandoverManifest,
    HandoverManifestItem,
    HandoverManifestItemStatus,
    HandoverManifestStatus,
    SenderType,
    VerificationInvalidationReason,
    VerificationMethod,
)
from pickup.domain.offline import (
    OfflineAuthorization,
    OfflineEventRecord,
    OfflineEventStatus,
    OfflineOperation,
    OfflineOutcomeCode,
    OfflineReconciliationCase,
    OfflineResourceType,
    OfflineStream,
    ReconciliationCaseStatus,
    ReconciliationResolution,
)
from pickup.domain.workforce import (
    OPEN_WORK_SESSION_STATUSES,
    DriverAvailabilityStatus,
    DriverCapability,
    DriverWorkSession,
    WorkSessionEndReason,
    WorkSessionPauseReason,
    WorkSessionStatus,
)
from pickup.infrastructure.persistence.models import (
    CourierChallengeRow,
    CourierManifestRow,
    DriverWorkSessionRow,
    HandoverManifestItemRow,
    HandoverManifestRow,
    OfflineAuthorizationRow,
    OfflineEventRow,
    OfflineStreamRow,
    ReconciliationCaseRow,
    TaskHistoryRow,
)


class _SessionRepo:
    """Base for repositories that write through the unit-of-work session."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def _require_session(self, operation: str):
        session = self._store._session
        if session is None:
            msg = f"{operation} outside transaction"
            raise RuntimeError(msg)
        return session

    def _read_session(self):
        return self._store._session


class TaskHistoryRepo(_SessionRepo):
    def append_entry(self, entry: TaskHistoryEntry) -> None:
        session = self._require_session("task history append")
        session.add(
            TaskHistoryRow(
                history_id=entry.history_id,
                pickup_task_id=entry.pickup_task_id,
                action=entry.action,
                actor_id=entry.actor_id,
                actor_role=entry.actor_role,
                previous_status=entry.previous_status,
                new_status=entry.new_status,
                occurred_at=entry.occurred_at,
                request_id=entry.request_id,
                details=dict(entry.details),
            )
        )
        session.flush()

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[TaskHistoryEntry, ...]:
        session = self._read_session()
        statement = (
            select(TaskHistoryRow)
            .where(TaskHistoryRow.pickup_task_id == pickup_task_id)
            .order_by(TaskHistoryRow.occurred_at)
        )
        if session is not None:
            rows = session.execute(statement).scalars().all()
            return tuple(_history_entry_from_row(row) for row in rows)
        with self._store.session_factory() as read_session:
            rows = read_session.execute(statement).scalars().all()
            return tuple(_history_entry_from_row(row) for row in rows)


class DriverWorkSessionRepo(_SessionRepo):
    def save_session(self, session_entity: DriverWorkSession) -> None:
        session = self._require_session("work session save")
        session.merge(_work_session_to_row(session_entity))
        session.flush()

    def get_session(self, session_id: UUID) -> DriverWorkSession | None:
        session = self._read_session()
        if session is not None:
            row = session.get(DriverWorkSessionRow, session_id)
            return _work_session_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.get(DriverWorkSessionRow, session_id)
            return _work_session_from_row(row) if row is not None else None

    def get_open_session_for_driver(self, driver_user_id: str) -> DriverWorkSession | None:
        statement = select(DriverWorkSessionRow).where(
            DriverWorkSessionRow.driver_user_id == driver_user_id,
            DriverWorkSessionRow.status.in_(
                [status.value for status in OPEN_WORK_SESSION_STATUSES]
            ),
        )
        session = self._read_session()
        if session is not None:
            # Serialize concurrent start/pause/resume/end for one driver.
            row = session.execute(statement.with_for_update()).scalar_one_or_none()
            return _work_session_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.execute(statement).scalar_one_or_none()
            return _work_session_from_row(row) if row is not None else None


class CourierChallengeRepo(_SessionRepo):
    def save_challenge(self, challenge: CourierChallenge) -> None:
        session = self._require_session("challenge save")
        session.merge(_challenge_to_row(challenge))
        session.flush()

    def get_challenge(self, challenge_id: UUID) -> CourierChallenge | None:
        session = self._read_session()
        if session is not None:
            row = session.get(CourierChallengeRow, challenge_id, with_for_update=True)
            return _challenge_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.get(CourierChallengeRow, challenge_id)
            return _challenge_from_row(row) if row is not None else None

    def list_open_for_task(self, pickup_task_id: UUID) -> tuple[CourierChallenge, ...]:
        statement = select(CourierChallengeRow).where(
            CourierChallengeRow.pickup_task_id == pickup_task_id,
            CourierChallengeRow.status.in_(
                [CourierChallengeStatus.ISSUED.value, CourierChallengeStatus.VERIFIED.value]
            ),
        )
        session = self._read_session()
        if session is not None:
            rows = session.execute(statement.with_for_update()).scalars().all()
            return tuple(_challenge_from_row(row) for row in rows)
        with self._store.session_factory() as read_session:
            rows = read_session.execute(statement).scalars().all()
            return tuple(_challenge_from_row(row) for row in rows)


class CourierManifestRepo(_SessionRepo):
    def save_manifest(self, manifest: CourierManifest) -> None:
        session = self._require_session("courier manifest save")
        session.merge(_courier_manifest_to_row(manifest))
        session.flush()

    def get_manifest(self, manifest_id: UUID) -> CourierManifest | None:
        session = self._read_session()
        if session is not None:
            row = session.get(CourierManifestRow, manifest_id)
            return _courier_manifest_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.get(CourierManifestRow, manifest_id)
            return _courier_manifest_from_row(row) if row is not None else None

    def list_open_for_task(self, pickup_task_id: UUID) -> tuple[CourierManifest, ...]:
        statement = select(CourierManifestRow).where(
            CourierManifestRow.pickup_task_id == pickup_task_id,
            CourierManifestRow.status.in_(
                [CourierManifestStatus.SUBMITTED.value, CourierManifestStatus.CONFIRMED.value]
            ),
        )
        session = self._read_session()
        if session is not None:
            rows = session.execute(statement.with_for_update()).scalars().all()
            return tuple(_courier_manifest_from_row(row) for row in rows)
        with self._store.session_factory() as read_session:
            rows = read_session.execute(statement).scalars().all()
            return tuple(_courier_manifest_from_row(row) for row in rows)


class HandoverManifestRepo(_SessionRepo):
    def save_manifest(self, manifest: HandoverManifest) -> None:
        session = self._require_session("handover manifest save")
        session.merge(_handover_manifest_to_row(manifest))
        session.flush()

    def get_manifest(self, manifest_id: UUID) -> HandoverManifest | None:
        session = self._read_session()
        if session is not None:
            row = session.get(HandoverManifestRow, manifest_id, with_for_update=True)
            return _handover_manifest_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.get(HandoverManifestRow, manifest_id)
            return _handover_manifest_from_row(row) if row is not None else None

    def list_active_for_driver(self, driver_user_id: str) -> tuple[HandoverManifest, ...]:
        statement = select(HandoverManifestRow).where(
            HandoverManifestRow.driver_user_id == driver_user_id,
            HandoverManifestRow.status.in_(
                [status.value for status in ACTIVE_HANDOVER_MANIFEST_STATUSES]
            ),
        )
        return tuple(
            _handover_manifest_from_row(row) for row in self._select(statement)
        )

    def save_item(self, item: HandoverManifestItem) -> None:
        session = self._require_session("handover item save")
        session.merge(_handover_item_to_row(item))
        session.flush()

    def list_items(self, manifest_id: UUID) -> tuple[HandoverManifestItem, ...]:
        statement = (
            select(HandoverManifestItemRow)
            .where(HandoverManifestItemRow.manifest_id == manifest_id)
            .order_by(HandoverManifestItemRow.added_at)
        )
        return tuple(_handover_item_from_row(row) for row in self._select(statement))

    def find_active_item_for_shipment(
        self, shipment_id: UUID
    ) -> HandoverManifestItem | None:
        statement = (
            select(HandoverManifestItemRow)
            .join(
                HandoverManifestRow,
                HandoverManifestRow.manifest_id == HandoverManifestItemRow.manifest_id,
            )
            .where(
                HandoverManifestItemRow.shipment_id == shipment_id,
                HandoverManifestRow.status.in_(
                    [status.value for status in ACTIVE_HANDOVER_MANIFEST_STATUSES]
                ),
            )
            .order_by(HandoverManifestItemRow.added_at.desc())
            .limit(1)
        )
        rows = self._select(statement)
        return _handover_item_from_row(rows[0]) if rows else None

    def find_custody_item_for_shipment(
        self, shipment_id: UUID
    ) -> HandoverManifestItem | None:
        released = (
            select(HandoverManifestItemRow)
            .where(
                HandoverManifestItemRow.shipment_id == shipment_id,
                HandoverManifestItemRow.status.in_(
                    [
                        HandoverManifestItemStatus.RECEIVED.value,
                        HandoverManifestItemStatus.DISCREPANCY.value,
                    ]
                ),
            )
            .order_by(HandoverManifestItemRow.added_at.desc())
            .limit(1)
        )
        rows = self._select(released)
        if rows:
            return _handover_item_from_row(rows[0])
        latest = (
            select(HandoverManifestItemRow)
            .where(HandoverManifestItemRow.shipment_id == shipment_id)
            .order_by(HandoverManifestItemRow.added_at.desc())
            .limit(1)
        )
        rows = self._select(latest)
        return _handover_item_from_row(rows[0]) if rows else None

    def _select(self, statement):
        session = self._read_session()
        if session is not None:
            return session.execute(statement).scalars().all()
        with self._store.session_factory() as read_session:
            return read_session.execute(statement).scalars().all()


class OfflineAuthorizationRepo(_SessionRepo):
    def save_authorization(self, authorization: OfflineAuthorization) -> None:
        session = self._require_session("offline authorization save")
        session.merge(_authorization_to_row(authorization))
        session.flush()

    def get_authorization(self, authorization_id: UUID) -> OfflineAuthorization | None:
        session = self._read_session()
        if session is not None:
            row = session.get(OfflineAuthorizationRow, authorization_id, with_for_update=True)
            return _authorization_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.get(OfflineAuthorizationRow, authorization_id)
            return _authorization_from_row(row) if row is not None else None

    def list_for_driver(self, driver_user_id: str) -> tuple[OfflineAuthorization, ...]:
        statement = (
            select(OfflineAuthorizationRow)
            .where(OfflineAuthorizationRow.driver_user_id == driver_user_id)
            .order_by(OfflineAuthorizationRow.issued_at.desc())
        )
        session = self._read_session()
        if session is not None:
            rows = session.execute(statement).scalars().all()
        else:
            with self._store.session_factory() as read_session:
                rows = read_session.execute(statement).scalars().all()
        return tuple(_authorization_from_row(row) for row in rows)


class OfflineStreamRepo(_SessionRepo):
    def save_stream(self, stream: OfflineStream) -> None:
        session = self._require_session("offline stream save")
        session.merge(_stream_to_row(stream))
        session.flush()

    def get_stream(self, stream_id: UUID) -> OfflineStream | None:
        session = self._read_session()
        if session is not None:
            # Serialize concurrent syncs of the same device stream.
            row = session.get(OfflineStreamRow, stream_id, with_for_update=True)
            return _stream_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.get(OfflineStreamRow, stream_id)
            return _stream_from_row(row) if row is not None else None


class OfflineEventRepo(_SessionRepo):
    def append_event(self, event: OfflineEventRecord) -> None:
        session = self._require_session("offline event append")
        session.add(_offline_event_to_row(event))
        session.flush()

    def update_event(self, event: OfflineEventRecord) -> None:
        session = self._require_session("offline event update")
        session.merge(_offline_event_to_row(event))
        session.flush()

    def find_by_operation_id(
        self, *, driver_user_id: str, operation_id: UUID
    ) -> OfflineEventRecord | None:
        statement = select(OfflineEventRow).where(
            OfflineEventRow.driver_user_id == driver_user_id,
            OfflineEventRow.operation_id == operation_id,
        )
        return self._one(statement)

    def find_by_stream_sequence(
        self, *, stream_id: UUID, sequence: int
    ) -> OfflineEventRecord | None:
        statement = select(OfflineEventRow).where(
            OfflineEventRow.stream_id == stream_id,
            OfflineEventRow.sequence == sequence,
        )
        return self._one(statement)

    def list_for_driver(self, driver_user_id: str) -> tuple[OfflineEventRecord, ...]:
        statement = (
            select(OfflineEventRow)
            .where(OfflineEventRow.driver_user_id == driver_user_id)
            .order_by(OfflineEventRow.sequence)
        )
        session = self._read_session()
        if session is not None:
            rows = session.execute(statement).scalars().all()
        else:
            with self._store.session_factory() as read_session:
                rows = read_session.execute(statement).scalars().all()
        return tuple(_offline_event_from_row(row) for row in rows)

    def _one(self, statement) -> OfflineEventRecord | None:
        session = self._read_session()
        if session is not None:
            row = session.execute(statement).scalar_one_or_none()
            return _offline_event_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.execute(statement).scalar_one_or_none()
            return _offline_event_from_row(row) if row is not None else None


class ReconciliationCaseRepo(_SessionRepo):
    def save_case(self, case: OfflineReconciliationCase) -> None:
        session = self._require_session("reconciliation case save")
        session.merge(_case_to_row(case))
        session.flush()

    def get_case(self, case_id: UUID) -> OfflineReconciliationCase | None:
        session = self._read_session()
        if session is not None:
            row = session.get(ReconciliationCaseRow, case_id, with_for_update=True)
            return _case_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.get(ReconciliationCaseRow, case_id)
            return _case_from_row(row) if row is not None else None

    def get_open_case_for_event(
        self, offline_event_row_id: UUID
    ) -> OfflineReconciliationCase | None:
        statement = select(ReconciliationCaseRow).where(
            ReconciliationCaseRow.offline_event_row_id == offline_event_row_id,
            ReconciliationCaseRow.status == ReconciliationCaseStatus.OPEN.value,
        )
        session = self._read_session()
        if session is not None:
            row = session.execute(statement).scalar_one_or_none()
            return _case_from_row(row) if row is not None else None
        with self._store.session_factory() as read_session:
            row = read_session.execute(statement).scalar_one_or_none()
            return _case_from_row(row) if row is not None else None

    def list_cases(
        self, *, driver_user_id: str | None = None, status: str | None = None
    ) -> tuple[OfflineReconciliationCase, ...]:
        statement = select(ReconciliationCaseRow)
        if driver_user_id is not None:
            statement = statement.where(
                ReconciliationCaseRow.driver_user_id == driver_user_id
            )
        if status is not None:
            statement = statement.where(ReconciliationCaseRow.status == status)
        statement = statement.order_by(ReconciliationCaseRow.opened_at.desc())
        session = self._read_session()
        if session is not None:
            rows = session.execute(statement).scalars().all()
        else:
            with self._store.session_factory() as read_session:
                rows = read_session.execute(statement).scalars().all()
        return tuple(_case_from_row(row) for row in rows)


# ------------------------------------------------------------------------ mappers


def _history_entry_from_row(row: TaskHistoryRow) -> TaskHistoryEntry:
    return TaskHistoryEntry(
        history_id=row.history_id,  # type: ignore[arg-type]
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        action=row.action,
        actor_id=row.actor_id,
        actor_role=row.actor_role,
        previous_status=row.previous_status,
        new_status=row.new_status,
        occurred_at=row.occurred_at,  # type: ignore[arg-type]
        request_id=row.request_id,
        details=dict(row.details or {}),
    )


def _work_session_to_row(entity: DriverWorkSession) -> DriverWorkSessionRow:
    return DriverWorkSessionRow(
        session_id=entity.session_id,
        driver_user_id=entity.driver_user_id,
        capability=entity.capability.value,
        status=entity.status.value,
        availability=entity.availability.value,
        started_at=entity.started_at,
        home_hub_id=entity.home_hub_id,
        paused_at=entity.paused_at,
        resumed_at=entity.resumed_at,
        ended_at=entity.ended_at,
        pause_reason=entity.pause_reason.value if entity.pause_reason else None,
        end_reason=entity.end_reason.value if entity.end_reason else None,
        notes=entity.notes,
        session_metadata=dict(entity.metadata),
        version=entity.version,
    )


def _work_session_from_row(row: DriverWorkSessionRow) -> DriverWorkSession:
    return DriverWorkSession(
        session_id=row.session_id,  # type: ignore[arg-type]
        driver_user_id=row.driver_user_id,
        capability=DriverCapability(row.capability),
        status=WorkSessionStatus(row.status),
        availability=DriverAvailabilityStatus(row.availability),
        started_at=row.started_at,  # type: ignore[arg-type]
        home_hub_id=row.home_hub_id,  # type: ignore[arg-type]
        paused_at=row.paused_at,  # type: ignore[arg-type]
        resumed_at=row.resumed_at,  # type: ignore[arg-type]
        ended_at=row.ended_at,  # type: ignore[arg-type]
        pause_reason=(
            WorkSessionPauseReason(row.pause_reason) if row.pause_reason else None
        ),
        end_reason=WorkSessionEndReason(row.end_reason) if row.end_reason else None,
        notes=row.notes,
        metadata=dict(row.session_metadata or {}),
        version=row.version,
    )


def _challenge_to_row(entity: CourierChallenge) -> CourierChallengeRow:
    return CourierChallengeRow(
        challenge_id=entity.challenge_id,
        pickup_task_id=entity.pickup_task_id,
        shipment_id=entity.shipment_id,
        assigned_driver_user_id=entity.assigned_driver_user_id,
        sender_type=entity.sender_type.value,
        assignment_fingerprint=entity.assignment_fingerprint,
        secret_hash=entity.secret_hash,
        method=entity.method.value,
        status=entity.status.value,
        issued_by_user_id=entity.issued_by_user_id,
        issued_at=entity.issued_at,
        expires_at=entity.expires_at,
        failed_attempt_count=entity.failed_attempt_count,
        locked_until=entity.locked_until,
        verified_at=entity.verified_at,
        verification_valid_until=entity.verification_valid_until,
        verifier_user_id=entity.verifier_user_id,
        consumed_at=entity.consumed_at,
        invalidated_at=entity.invalidated_at,
        invalidation_reason=(
            entity.invalidation_reason.value if entity.invalidation_reason else None
        ),
    )


def _challenge_from_row(row: CourierChallengeRow) -> CourierChallenge:
    return CourierChallenge(
        challenge_id=row.challenge_id,  # type: ignore[arg-type]
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        shipment_id=row.shipment_id,  # type: ignore[arg-type]
        assigned_driver_user_id=row.assigned_driver_user_id,
        sender_type=SenderType(row.sender_type),
        assignment_fingerprint=row.assignment_fingerprint,
        secret_hash=row.secret_hash,
        method=VerificationMethod(row.method),
        status=CourierChallengeStatus(row.status),
        issued_by_user_id=row.issued_by_user_id,
        issued_at=row.issued_at,  # type: ignore[arg-type]
        expires_at=row.expires_at,  # type: ignore[arg-type]
        failed_attempt_count=row.failed_attempt_count,
        locked_until=row.locked_until,  # type: ignore[arg-type]
        verified_at=row.verified_at,  # type: ignore[arg-type]
        verification_valid_until=row.verification_valid_until,  # type: ignore[arg-type]
        verifier_user_id=row.verifier_user_id,
        consumed_at=row.consumed_at,  # type: ignore[arg-type]
        invalidated_at=row.invalidated_at,  # type: ignore[arg-type]
        invalidation_reason=(
            VerificationInvalidationReason(row.invalidation_reason)
            if row.invalidation_reason
            else None
        ),
    )


def _courier_manifest_to_row(entity: CourierManifest) -> CourierManifestRow:
    return CourierManifestRow(
        manifest_id=entity.manifest_id,
        pickup_task_id=entity.pickup_task_id,
        shipment_id=entity.shipment_id,
        manifest_digest=entity.manifest_digest,
        status=entity.status.value,
        submitted_by_user_id=entity.submitted_by_user_id,
        submitted_at=entity.submitted_at,
        confirmed_by_user_id=entity.confirmed_by_user_id,
        confirmed_at=entity.confirmed_at,
        confirmation_valid_until=entity.confirmation_valid_until,
        consumed_at=entity.consumed_at,
        invalidated_at=entity.invalidated_at,
        invalidation_reason=(
            entity.invalidation_reason.value if entity.invalidation_reason else None
        ),
    )


def _courier_manifest_from_row(row: CourierManifestRow) -> CourierManifest:
    return CourierManifest(
        manifest_id=row.manifest_id,  # type: ignore[arg-type]
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        shipment_id=row.shipment_id,  # type: ignore[arg-type]
        manifest_digest=row.manifest_digest,
        status=CourierManifestStatus(row.status),
        submitted_by_user_id=row.submitted_by_user_id,
        submitted_at=row.submitted_at,  # type: ignore[arg-type]
        confirmed_by_user_id=row.confirmed_by_user_id,
        confirmed_at=row.confirmed_at,  # type: ignore[arg-type]
        confirmation_valid_until=row.confirmation_valid_until,  # type: ignore[arg-type]
        consumed_at=row.consumed_at,  # type: ignore[arg-type]
        invalidated_at=row.invalidated_at,  # type: ignore[arg-type]
        invalidation_reason=(
            VerificationInvalidationReason(row.invalidation_reason)
            if row.invalidation_reason
            else None
        ),
    )


def _handover_manifest_to_row(entity: HandoverManifest) -> HandoverManifestRow:
    return HandoverManifestRow(
        manifest_id=entity.manifest_id,
        manifest_code=entity.manifest_code,
        driver_user_id=entity.driver_user_id,
        hub_id=entity.hub_id,
        status=entity.status.value,
        expected_count=entity.expected_count,
        received_count=entity.received_count,
        missing_count=entity.missing_count,
        discrepancy_count=entity.discrepancy_count,
        created_at=entity.created_at,
        ready_at=entity.ready_at,
        arrived_at_hub_at=entity.arrived_at_hub_at,
        completed_at=entity.completed_at,
        cancelled_at=entity.cancelled_at,
        notes=entity.notes,
        manifest_metadata=dict(entity.metadata),
        version=entity.version,
    )


def _handover_manifest_from_row(row: HandoverManifestRow) -> HandoverManifest:
    return HandoverManifest(
        manifest_id=row.manifest_id,  # type: ignore[arg-type]
        manifest_code=row.manifest_code,
        driver_user_id=row.driver_user_id,
        hub_id=row.hub_id,  # type: ignore[arg-type]
        status=HandoverManifestStatus(row.status),
        expected_count=row.expected_count,
        received_count=row.received_count,
        missing_count=row.missing_count,
        discrepancy_count=row.discrepancy_count,
        created_at=row.created_at,  # type: ignore[arg-type]
        ready_at=row.ready_at,  # type: ignore[arg-type]
        arrived_at_hub_at=row.arrived_at_hub_at,  # type: ignore[arg-type]
        completed_at=row.completed_at,  # type: ignore[arg-type]
        cancelled_at=row.cancelled_at,  # type: ignore[arg-type]
        notes=row.notes,
        metadata=dict(row.manifest_metadata or {}),
        version=row.version,
    )


def _handover_item_to_row(entity: HandoverManifestItem) -> HandoverManifestItemRow:
    return HandoverManifestItemRow(
        item_id=entity.item_id,
        manifest_id=entity.manifest_id,
        pickup_task_id=entity.pickup_task_id,
        shipment_id=entity.shipment_id,
        status=entity.status.value,
        added_at=entity.added_at,
        received_at=entity.received_at,
        received_by_user_id=entity.received_by_user_id,
        received_hub_id=entity.received_hub_id,
        discrepancy_reason=(
            entity.discrepancy_reason.value if entity.discrepancy_reason else None
        ),
        notes=entity.notes,
    )


def _handover_item_from_row(row: HandoverManifestItemRow) -> HandoverManifestItem:
    return HandoverManifestItem(
        item_id=row.item_id,  # type: ignore[arg-type]
        manifest_id=row.manifest_id,  # type: ignore[arg-type]
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        shipment_id=row.shipment_id,  # type: ignore[arg-type]
        status=HandoverManifestItemStatus(row.status),
        added_at=row.added_at,  # type: ignore[arg-type]
        received_at=row.received_at,  # type: ignore[arg-type]
        received_by_user_id=row.received_by_user_id,
        received_hub_id=row.received_hub_id,  # type: ignore[arg-type]
        discrepancy_reason=(
            HandoverDiscrepancyReason(row.discrepancy_reason)
            if row.discrepancy_reason
            else None
        ),
        notes=row.notes,
    )


def _authorization_to_row(entity: OfflineAuthorization) -> OfflineAuthorizationRow:
    return OfflineAuthorizationRow(
        authorization_id=entity.authorization_id,
        driver_user_id=entity.driver_user_id,
        device_id_hash=entity.device_id_hash,
        resource_type=entity.resource_type.value,
        resource_id=entity.resource_id,
        assignment_revision=entity.assignment_revision,
        permitted_operations=[item.value for item in entity.permitted_operations],
        token_hash=entity.token_hash,
        issued_at=entity.issued_at,
        expires_at=entity.expires_at,
        sync_deadline=entity.sync_deadline,
        resource_snapshot=dict(entity.resource_snapshot),
        revoked_at=entity.revoked_at,
        revoked_reason=entity.revoked_reason,
    )


def _authorization_from_row(row: OfflineAuthorizationRow) -> OfflineAuthorization:
    return OfflineAuthorization(
        authorization_id=row.authorization_id,  # type: ignore[arg-type]
        driver_user_id=row.driver_user_id,
        device_id_hash=row.device_id_hash,
        resource_type=OfflineResourceType(row.resource_type),
        resource_id=row.resource_id,  # type: ignore[arg-type]
        assignment_revision=row.assignment_revision,
        permitted_operations=tuple(
            OfflineOperation(value) for value in (row.permitted_operations or [])
        ),
        token_hash=row.token_hash,
        issued_at=row.issued_at,  # type: ignore[arg-type]
        expires_at=row.expires_at,  # type: ignore[arg-type]
        sync_deadline=row.sync_deadline,  # type: ignore[arg-type]
        resource_snapshot=dict(row.resource_snapshot or {}),
        revoked_at=row.revoked_at,  # type: ignore[arg-type]
        revoked_reason=row.revoked_reason,
    )


def _stream_to_row(entity: OfflineStream) -> OfflineStreamRow:
    return OfflineStreamRow(
        stream_id=entity.stream_id,
        authorization_id=entity.authorization_id,
        driver_user_id=entity.driver_user_id,
        device_id_hash=entity.device_id_hash,
        last_contiguous_sequence=entity.last_contiguous_sequence,
        last_received_at=entity.last_received_at,
    )


def _stream_from_row(row: OfflineStreamRow) -> OfflineStream:
    return OfflineStream(
        stream_id=row.stream_id,  # type: ignore[arg-type]
        authorization_id=row.authorization_id,  # type: ignore[arg-type]
        driver_user_id=row.driver_user_id,
        device_id_hash=row.device_id_hash,
        last_contiguous_sequence=row.last_contiguous_sequence,
        last_received_at=row.last_received_at,  # type: ignore[arg-type]
    )


def _offline_event_to_row(entity: OfflineEventRecord) -> OfflineEventRow:
    return OfflineEventRow(
        event_row_id=entity.event_row_id,
        stream_id=entity.stream_id,
        authorization_id=entity.authorization_id,
        driver_user_id=entity.driver_user_id,
        operation_id=entity.operation_id,
        sequence=entity.sequence,
        operation=entity.operation.value,
        resource_type=entity.resource_type.value,
        resource_id=entity.resource_id,
        assignment_revision=entity.assignment_revision,
        captured_at=entity.captured_at,
        payload_fingerprint=entity.payload_fingerprint,
        payload=dict(entity.payload),
        status=entity.status.value,
        outcome_code=entity.outcome_code.value,
        outcome=dict(entity.outcome),
        processed_at=entity.processed_at,
        replayed=entity.replayed,
    )


def _offline_event_from_row(row: OfflineEventRow) -> OfflineEventRecord:
    return OfflineEventRecord(
        event_row_id=row.event_row_id,  # type: ignore[arg-type]
        stream_id=row.stream_id,  # type: ignore[arg-type]
        authorization_id=row.authorization_id,  # type: ignore[arg-type]
        driver_user_id=row.driver_user_id,
        operation_id=row.operation_id,  # type: ignore[arg-type]
        sequence=row.sequence,
        operation=OfflineOperation(row.operation),
        resource_type=OfflineResourceType(row.resource_type),
        resource_id=row.resource_id,  # type: ignore[arg-type]
        assignment_revision=row.assignment_revision,
        captured_at=row.captured_at,  # type: ignore[arg-type]
        payload_fingerprint=row.payload_fingerprint,
        payload=dict(row.payload or {}),
        status=OfflineEventStatus(row.status),
        outcome_code=OfflineOutcomeCode(row.outcome_code),
        outcome=dict(row.outcome or {}),
        processed_at=row.processed_at,  # type: ignore[arg-type]
        replayed=row.replayed,
    )


def _case_to_row(entity: OfflineReconciliationCase) -> ReconciliationCaseRow:
    return ReconciliationCaseRow(
        case_id=entity.case_id,
        offline_event_row_id=entity.offline_event_row_id,
        driver_user_id=entity.driver_user_id,
        resource_type=entity.resource_type.value,
        resource_id=entity.resource_id,
        status=entity.status.value,
        reason_code=entity.reason_code.value,
        reason_detail=entity.reason_detail,
        authoritative_state=dict(entity.authoritative_state),
        submitted_event=dict(entity.submitted_event),
        custody_implication=entity.custody_implication,
        opened_at=entity.opened_at,
        resolved_at=entity.resolved_at,
        resolved_by_user_id=entity.resolved_by_user_id,
        resolution=entity.resolution.value if entity.resolution else None,
        resolution_notes=entity.resolution_notes,
    )


def _case_from_row(row: ReconciliationCaseRow) -> OfflineReconciliationCase:
    return OfflineReconciliationCase(
        case_id=row.case_id,  # type: ignore[arg-type]
        offline_event_row_id=row.offline_event_row_id,  # type: ignore[arg-type]
        driver_user_id=row.driver_user_id,
        resource_type=OfflineResourceType(row.resource_type),
        resource_id=row.resource_id,  # type: ignore[arg-type]
        status=ReconciliationCaseStatus(row.status),
        reason_code=OfflineOutcomeCode(row.reason_code),
        reason_detail=row.reason_detail,
        authoritative_state=dict(row.authoritative_state or {}),
        submitted_event=dict(row.submitted_event or {}),
        custody_implication=row.custody_implication,
        opened_at=row.opened_at,  # type: ignore[arg-type]
        resolved_at=row.resolved_at,  # type: ignore[arg-type]
        resolved_by_user_id=row.resolved_by_user_id,
        resolution=(
            ReconciliationResolution(row.resolution) if row.resolution else None
        ),
        resolution_notes=row.resolution_notes,
    )
