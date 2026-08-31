"""In-memory persistence adapters for deterministic tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from legacy_event_bridge.domain.errors import SourceTableNotAllowedError
from legacy_event_bridge.domain.types import (
    ALLOWLISTED_SOURCE_TABLES,
    CdcChange,
    CheckpointRecord,
    LandingRecord,
    MappingState,
    OutboxRecord,
    SourceRowIdentity,
)
from legacy_event_bridge.ports import TransactionPort


@dataclass
class _MemoryTransaction(TransactionPort):
    store: MemoryBridgeStore
    committed: bool = False
    rolled_back: bool = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True
        self.store._rollback_snapshot()


@dataclass
class MemoryBridgeStore:
    """Single in-memory store backing landing, checkpoint, and outbox ports."""

    landings: dict[UUID, LandingRecord] = field(default_factory=dict)
    landing_dedupe: dict[tuple[str, str, str], UUID] = field(default_factory=dict)
    checkpoints: dict[str, CheckpointRecord] = field(default_factory=dict)
    outbox: dict[UUID, OutboxRecord] = field(default_factory=dict)
    outbox_by_event_id: dict[UUID, UUID] = field(default_factory=dict)
    feedback_log: list[tuple[str, str]] = field(default_factory=list)
    publish_log: list[tuple[str, str, str]] = field(default_factory=list)
    publish_should_ack: bool = True
    feedback_should_succeed: bool = True
    _snapshot: dict | None = field(default=None, repr=False)

    def begin(self) -> _MemoryTransaction:
        self._snapshot = {
            "landings": dict(self.landings),
            "landing_dedupe": dict(self.landing_dedupe),
            "checkpoints": dict(self.checkpoints),
            "outbox": dict(self.outbox),
            "outbox_by_event_id": dict(self.outbox_by_event_id),
        }
        return _MemoryTransaction(store=self)

    def _rollback_snapshot(self) -> None:
        if self._snapshot is None:
            return
        self.landings = self._snapshot["landings"]
        self.landing_dedupe = self._snapshot["landing_dedupe"]
        self.checkpoints = self._snapshot["checkpoints"]
        self.outbox = self._snapshot["outbox"]
        self.outbox_by_event_id = self._snapshot["outbox_by_event_id"]
        self._snapshot = None

    # LandingStorePort
    def insert_landing(
        self,
        tx: TransactionPort,
        *,
        change: CdcChange,
        mapper_version: str,
    ) -> tuple[LandingRecord | None, bool]:
        if change.source_table not in ALLOWLISTED_SOURCE_TABLES:
            raise SourceTableNotAllowedError(change.source_table)

        identity = SourceRowIdentity(
            source_system=change.source_system,
            source_table=change.source_table,
            source_pk=change.source_pk,
        )
        key = identity.dedupe_key()
        if key in self.landing_dedupe:
            return None, False

        record = LandingRecord(
            id=uuid4(),
            identity=identity,
            source_position=change.source_position,
            mapper_version=mapper_version,
            normalized_fields=dict(change.normalized_fields),
            received_at=change.received_at,
            mapping_state=MappingState.PENDING,
        )
        self.landings[record.id] = record
        self.landing_dedupe[key] = record.id
        return record, True

    def get_by_identity(
        self,
        *,
        source_system: str,
        source_table: str,
        source_pk: UUID,
    ) -> LandingRecord | None:
        landing_id = self.landing_dedupe.get((source_system, source_table, str(source_pk)))
        if landing_id is None:
            return None
        return self.landings.get(landing_id)

    def list_pending_mapping(self, *, limit: int) -> list[LandingRecord]:
        pending = [
            row
            for row in self.landings.values()
            if row.mapping_state is MappingState.PENDING
        ]
        return pending[:limit]

    def mark_mapped(
        self,
        tx: TransactionPort,
        *,
        landing_id: UUID,
        mapped_at: datetime,
    ) -> None:
        row = self.landings[landing_id]
        self.landings[landing_id] = LandingRecord(
            id=row.id,
            identity=row.identity,
            source_position=row.source_position,
            mapper_version=row.mapper_version,
            normalized_fields=row.normalized_fields,
            received_at=row.received_at,
            mapping_state=MappingState.MAPPED,
            mapped_at=mapped_at,
            quarantined_at=row.quarantined_at,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
        )

    def mark_mapping_failed(
        self,
        tx: TransactionPort,
        *,
        landing_id: UUID,
        error_code: str,
        error_message: str,
        quarantine: bool,
        at: datetime,
    ) -> None:
        row = self.landings[landing_id]
        state = MappingState.QUARANTINED if quarantine else MappingState.PENDING
        self.landings[landing_id] = LandingRecord(
            id=row.id,
            identity=row.identity,
            source_position=row.source_position,
            mapper_version=row.mapper_version,
            normalized_fields=row.normalized_fields,
            received_at=row.received_at,
            mapping_state=state,
            mapped_at=row.mapped_at,
            quarantined_at=at if quarantine else row.quarantined_at,
            last_error_code=error_code,
            last_error_message=error_message,
        )

    # CheckpointStorePort
    def get(self, *, capture_source: str) -> CheckpointRecord | None:
        return self.checkpoints.get(capture_source)

    def update_durable_landed(
        self,
        tx: TransactionPort,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None:
        existing = self.checkpoints.get(capture_source)
        self.checkpoints[capture_source] = CheckpointRecord(
            capture_source=capture_source,
            last_durably_landed_position=position,
            last_feedback_eligible_position=existing.last_feedback_eligible_position
            if existing
            else None,
            last_external_slot_advanced_position=existing.last_external_slot_advanced_position
            if existing
            else None,
            updated_at=at,
        )

    def mark_feedback_eligible(
        self,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None:
        existing = self.checkpoints.get(capture_source)
        self.checkpoints[capture_source] = CheckpointRecord(
            capture_source=capture_source,
            last_durably_landed_position=existing.last_durably_landed_position
            if existing
            else position,
            last_feedback_eligible_position=position,
            last_external_slot_advanced_position=existing.last_external_slot_advanced_position
            if existing
            else None,
            updated_at=at,
        )

    def mark_external_slot_advanced(
        self,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None:
        existing = self.checkpoints.get(capture_source)
        self.checkpoints[capture_source] = CheckpointRecord(
            capture_source=capture_source,
            last_durably_landed_position=existing.last_durably_landed_position
            if existing
            else position,
            last_feedback_eligible_position=existing.last_feedback_eligible_position
            if existing
            else position,
            last_external_slot_advanced_position=position,
            updated_at=at,
        )

    # OutboxStorePort
    def insert(
        self,
        tx: TransactionPort,
        *,
        event_id: UUID,
        subject: str,
        payload_json: dict,
        landing_id: UUID,
        max_attempts: int,
        at: datetime,
    ) -> OutboxRecord:
        if event_id in self.outbox_by_event_id:
            return self.outbox[ self.outbox_by_event_id[event_id]]

        row = OutboxRecord(
            id=uuid4(),
            event_id=event_id,
            subject=subject,
            payload_json=payload_json,
            status="pending",
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=at,
            processing_owner=None,
            processing_until=None,
            published_at=None,
            last_error_code=None,
            last_error_message=None,
            created_at=at,
            landing_id=landing_id,
        )
        self.outbox[row.id] = row
        self.outbox_by_event_id[event_id] = row.id
        return row

    def recover_stale_processing(self, *, now: datetime) -> int:
        recovered = 0
        for row_id, row in list(self.outbox.items()):
            if row.status != "processing":
                continue
            if row.processing_until and row.processing_until < now:
                self.outbox[row_id] = OutboxRecord(
                    id=row.id,
                    event_id=row.event_id,
                    subject=row.subject,
                    payload_json=row.payload_json,
                    status="pending",
                    attempt_count=row.attempt_count,
                    max_attempts=row.max_attempts,
                    next_attempt_at=now,
                    processing_owner=None,
                    processing_until=None,
                    published_at=row.published_at,
                    last_error_code="STALE_LEASE",
                    last_error_message="stale_processing_lease",
                    created_at=row.created_at,
                    landing_id=row.landing_id,
                )
                recovered += 1
        return recovered

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]:
        claimable = [
            row
            for row in self.outbox.values()
            if row.status == "pending" and row.next_attempt_at <= now
        ]
        claimable.sort(key=lambda row: row.next_attempt_at)
        claimed: list[OutboxRecord] = []
        for row in claimable[:batch_size]:
            updated = OutboxRecord(
                id=row.id,
                event_id=row.event_id,
                subject=row.subject,
                payload_json=row.payload_json,
                status="processing",
                attempt_count=row.attempt_count + 1,
                max_attempts=row.max_attempts,
                next_attempt_at=row.next_attempt_at,
                processing_owner=owner,
                processing_until=lease_until,
                published_at=row.published_at,
                last_error_code=row.last_error_code,
                last_error_message=row.last_error_message,
                created_at=row.created_at,
                landing_id=row.landing_id,
            )
            self.outbox[row.id] = updated
            claimed.append(updated)
        return claimed

    def apply_publish_decision(
        self,
        *,
        outbox_id: UUID,
        status: str,
        clear_owner: bool,
        clear_lease: bool,
        published_at: datetime | None,
        next_attempt_at: datetime | None,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> None:
        row = self.outbox[outbox_id]
        self.outbox[outbox_id] = OutboxRecord(
            id=row.id,
            event_id=row.event_id,
            subject=row.subject,
            payload_json=row.payload_json,
            status=status,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            next_attempt_at=next_attempt_at or row.next_attempt_at,
            processing_owner=None if clear_owner else row.processing_owner,
            processing_until=None if clear_lease else row.processing_until,
            published_at=published_at or row.published_at,
            last_error_code=last_error_code,
            last_error_message=last_error_message,
            created_at=row.created_at,
            landing_id=row.landing_id,
        )

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        row_id = self.outbox_by_event_id.get(event_id)
        if row_id is None:
            return None
        return self.outbox.get(row_id)

    # PublisherPort
    def publish(
        self,
        *,
        subject: str,
        payload_json: dict,
        transport_msg_id: str,
    ) -> bool:
        self.publish_log.append((subject, transport_msg_id, payload_json.get("event_id", "")))
        return self.publish_should_ack

    # ReplicationFeedbackPort
    def send_feedback(self, *, capture_source: str, position: str) -> bool:
        self.feedback_log.append((capture_source, position))
        return self.feedback_should_succeed


class FixtureCdcAdapter:
    """Fixture-fed CDC adapter for deterministic tests."""

    def __init__(self, *, fixtures: list[CdcChange] | None = None) -> None:
        self._queue: list[CdcChange] = list(fixtures or [])

    def enqueue(self, change: CdcChange) -> None:
        self._queue.append(change)

    def poll_batch(self, *, capture_source: str, limit: int) -> list[CdcChange]:
        batch = [change for change in self._queue if change.capture_slot == capture_source][:limit]
        self._queue = [change for change in self._queue if change not in batch]
        return batch
