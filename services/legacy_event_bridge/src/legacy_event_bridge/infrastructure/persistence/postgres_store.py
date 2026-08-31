"""PostgreSQL persistence adapter for Bridge landing, checkpoint, and outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from legacy_event_bridge.domain.errors import SourceTableNotAllowedError
from legacy_event_bridge.domain.types import (
    CdcChange,
    CheckpointRecord,
    LandingRecord,
    MappingState,
    OutboxRecord,
    SourceRowIdentity,
    allowlisted_source_tables,
)
from legacy_event_bridge.infrastructure.persistence.models import (
    BridgeCheckpointRow,
    BridgeLandingRow,
    BridgeOutboxRow,
)
from legacy_event_bridge.ports import TransactionPort


class SqlAlchemyTransaction(TransactionPort):
    def __init__(self, session: Session) -> None:
        self._session = session
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self._session.commit()
        self.committed = True

    def rollback(self) -> None:
        self._session.rollback()
        self.rolled_back = True


@dataclass
class SqlAlchemyBridgeStore:
    """Service-owned PostgreSQL adapter implementing Bridge persistence ports."""

    session_factory: sessionmaker[Session]

    def begin(self) -> SqlAlchemyTransaction:
        return SqlAlchemyTransaction(self.session_factory())

    def insert_landing(
        self,
        tx: TransactionPort,
        *,
        change: CdcChange,
        mapper_version: str,
    ) -> tuple[LandingRecord | None, bool]:
        if change.source_table not in allowlisted_source_tables():
            raise SourceTableNotAllowedError(change.source_table)
        session = _session(tx)
        stmt = (
            insert(BridgeLandingRow)
            .values(
                id=uuid4(),
                source_system=change.source_system,
                source_table=change.source_table,
                source_pk=change.source_pk,
                source_position=change.source_position,
                mapper_version=mapper_version,
                normalized_fields=change.normalized_fields,
                received_at=change.received_at,
                mapping_state=MappingState.PENDING.value,
                mapping_attempt_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=["source_system", "source_table", "source_pk"],
            )
            .returning(BridgeLandingRow.id)
        )
        inserted_id = session.execute(stmt).scalar_one_or_none()
        if inserted_id is None:
            return None, False
        row = session.get(BridgeLandingRow, inserted_id)
        assert row is not None
        return _landing_from_row(row), True

    def get_by_identity(
        self,
        *,
        source_system: str,
        source_table: str,
        source_pk: UUID,
    ) -> LandingRecord | None:
        with self.session_factory() as session:
            stmt = select(BridgeLandingRow).where(
                BridgeLandingRow.source_system == source_system,
                BridgeLandingRow.source_table == source_table,
                BridgeLandingRow.source_pk == source_pk,
            )
            row = session.execute(stmt).scalar_one_or_none()
            return _landing_from_row(row) if row is not None else None

    def get_by_id(self, *, landing_id: UUID) -> LandingRecord | None:
        with self.session_factory() as session:
            row = session.get(BridgeLandingRow, landing_id)
            return _landing_from_row(row) if row is not None else None

    def list_pending_mapping(self, *, limit: int) -> list[LandingRecord]:
        with self.session_factory() as session:
            stmt = (
                select(BridgeLandingRow)
                .where(BridgeLandingRow.mapping_state == MappingState.PENDING.value)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            rows = session.execute(stmt).scalars().all()
            return [_landing_from_row(row) for row in rows]

    def mark_mapped(
        self,
        tx: TransactionPort,
        *,
        landing_id: UUID,
        mapped_at: datetime,
    ) -> None:
        session = _session(tx)
        session.execute(
            update(BridgeLandingRow)
            .where(BridgeLandingRow.id == landing_id)
            .values(mapping_state=MappingState.MAPPED.value, mapped_at=mapped_at)
        )

    def mark_mapping_failed(
        self,
        tx: TransactionPort,
        *,
        landing_id: UUID,
        error_code: str,
        error_message: str,
        quarantine: bool,
        attempt_count: int,
        at: datetime,
    ) -> None:
        session = _session(tx)
        state = MappingState.QUARANTINED if quarantine else MappingState.PENDING
        values: dict[str, object] = {
            "mapping_state": state.value,
            "mapping_attempt_count": attempt_count,
            "last_error_code": error_code,
            "last_error_message": error_message,
        }
        if quarantine:
            values["quarantined_at"] = at
        session.execute(
            update(BridgeLandingRow).where(BridgeLandingRow.id == landing_id).values(**values)
        )

    def get(self, *, capture_source: str) -> CheckpointRecord | None:
        with self.session_factory() as session:
            row = session.get(BridgeCheckpointRow, capture_source)
            return _checkpoint_from_row(row) if row is not None else None

    def update_durable_landed(
        self,
        tx: TransactionPort,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None:
        session = _session(tx)
        existing = session.get(BridgeCheckpointRow, capture_source)
        if existing is None:
            session.add(
                BridgeCheckpointRow(
                    capture_source=capture_source,
                    last_durably_landed_position=position,
                    last_feedback_eligible_position=None,
                    last_external_slot_advanced_position=None,
                    updated_at=at,
                )
            )
            return
        existing.last_durably_landed_position = position
        existing.updated_at = at

    def mark_feedback_eligible(
        self,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None:
        with self.session_factory() as session:
            existing = session.get(BridgeCheckpointRow, capture_source)
            if existing is None:
                session.add(
                    BridgeCheckpointRow(
                        capture_source=capture_source,
                        last_durably_landed_position=position,
                        last_feedback_eligible_position=position,
                        last_external_slot_advanced_position=None,
                        updated_at=at,
                    )
                )
            else:
                existing.last_feedback_eligible_position = position
                existing.updated_at = at
            session.commit()

    def mark_external_slot_advanced(
        self,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None:
        with self.session_factory() as session:
            existing = session.get(BridgeCheckpointRow, capture_source)
            if existing is None:
                session.add(
                    BridgeCheckpointRow(
                        capture_source=capture_source,
                        last_durably_landed_position=position,
                        last_feedback_eligible_position=position,
                        last_external_slot_advanced_position=position,
                        updated_at=at,
                    )
                )
            else:
                existing.last_external_slot_advanced_position = position
                existing.updated_at = at
            session.commit()

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
        session = _session(tx)
        row = BridgeOutboxRow(
            id=uuid4(),
            event_id=event_id,
            subject=subject,
            payload_json=payload_json,
            status="pending",
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=at,
            created_at=at,
            landing_id=landing_id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.execute(
                select(BridgeOutboxRow).where(BridgeOutboxRow.event_id == event_id)
            ).scalar_one()
            return _outbox_from_row(existing)
        return _outbox_from_row(row)

    def recover_stale_processing(self, *, now: datetime) -> int:
        with self.session_factory() as session:
            result = session.execute(
                update(BridgeOutboxRow)
                .where(
                    BridgeOutboxRow.status == "processing",
                    BridgeOutboxRow.processing_until < now,
                )
                .values(
                    status="pending",
                    processing_owner=None,
                    processing_until=None,
                    next_attempt_at=now,
                    last_error_code="STALE_LEASE",
                    last_error_message="stale_processing_lease",
                )
            )
            session.commit()
            return result.rowcount or 0

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]:
        with self.session_factory() as session:
            candidate_ids = (
                session.execute(
                    select(BridgeOutboxRow.id)
                    .where(
                        BridgeOutboxRow.status == "pending",
                        BridgeOutboxRow.next_attempt_at <= now,
                    )
                    .order_by(BridgeOutboxRow.next_attempt_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )
            if not candidate_ids:
                return []
            session.execute(
                update(BridgeOutboxRow)
                .where(BridgeOutboxRow.id.in_(candidate_ids))
                .values(
                    status="processing",
                    processing_owner=owner,
                    processing_until=lease_until,
                    attempt_count=BridgeOutboxRow.attempt_count + 1,
                )
            )
            rows = session.execute(
                select(BridgeOutboxRow).where(BridgeOutboxRow.id.in_(candidate_ids))
            ).scalars()
            claimed = [_outbox_from_row(row) for row in rows]
            session.commit()
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
        with self.session_factory() as session:
            row = session.get(BridgeOutboxRow, outbox_id)
            if row is None:
                msg = f"outbox row missing: {outbox_id}"
                raise KeyError(msg)
            row.status = status
            if published_at is not None:
                row.published_at = published_at
            if next_attempt_at is not None:
                row.next_attempt_at = next_attempt_at
            row.last_error_code = last_error_code
            row.last_error_message = last_error_message
            if clear_owner:
                row.processing_owner = None
            if clear_lease:
                row.processing_until = None
            session.commit()

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        with self.session_factory() as session:
            row = session.execute(
                select(BridgeOutboxRow).where(BridgeOutboxRow.event_id == event_id)
            ).scalar_one_or_none()
            return _outbox_from_row(row) if row is not None else None


def _session(tx: TransactionPort) -> Session:
    if not isinstance(tx, SqlAlchemyTransaction):
        msg = "expected SqlAlchemyTransaction"
        raise TypeError(msg)
    return tx._session


def _landing_from_row(row: BridgeLandingRow) -> LandingRecord:
    return LandingRecord(
        id=row.id,  # type: ignore[arg-type]
        identity=SourceRowIdentity(
            source_system=row.source_system,
            source_table=row.source_table,
            source_pk=row.source_pk,  # type: ignore[arg-type]
        ),
        source_position=row.source_position,
        mapper_version=row.mapper_version,
        normalized_fields=dict(row.normalized_fields),
        received_at=row.received_at,  # type: ignore[arg-type]
        mapping_state=MappingState(row.mapping_state),
        mapping_attempt_count=row.mapping_attempt_count,
        mapped_at=row.mapped_at,  # type: ignore[arg-type]
        quarantined_at=row.quarantined_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
    )


def _checkpoint_from_row(row: BridgeCheckpointRow) -> CheckpointRecord:
    return CheckpointRecord(
        capture_source=row.capture_source,
        last_durably_landed_position=row.last_durably_landed_position,
        last_feedback_eligible_position=row.last_feedback_eligible_position,
        last_external_slot_advanced_position=row.last_external_slot_advanced_position,
        updated_at=row.updated_at,  # type: ignore[arg-type]
    )


def _outbox_from_row(row: BridgeOutboxRow) -> OutboxRecord:
    return OutboxRecord(
        id=row.id,  # type: ignore[arg-type]
        event_id=row.event_id,  # type: ignore[arg-type]
        subject=row.subject,
        payload_json=dict(row.payload_json),
        status=row.status,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,  # type: ignore[arg-type]
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,  # type: ignore[arg-type]
        published_at=row.published_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,  # type: ignore[arg-type]
        landing_id=row.landing_id,  # type: ignore[arg-type]
    )
