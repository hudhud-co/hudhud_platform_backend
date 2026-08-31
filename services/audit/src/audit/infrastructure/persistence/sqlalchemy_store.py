"""PostgreSQL inbox, observation projection, and unit-of-work adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from messaging_conformance.enums import InboxStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from audit.domain.sanitize import sanitize_error_message
from audit.domain.types import InboxRow, LegacyAuditObservation
from audit.infrastructure.persistence.models import IntegrationInboxRow, LegacyAuditObservationRow


@dataclass
class SqlAlchemyAuditStore:
    """Transactional inbox plus observation projection store."""

    session_factory: sessionmaker[Session]
    _session: Session | None = None
    _pending_inbox: dict[tuple[str, UUID], IntegrationInboxRow] | None = None
    _pending_observations: dict[UUID, LegacyAuditObservationRow] | None = None

    def begin(self) -> None:
        self._session = self.session_factory()
        self._pending_inbox = {}
        self._pending_observations = {}

    def commit(self) -> None:
        if self._session is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        assert self._pending_inbox is not None
        assert self._pending_observations is not None
        for row in self._pending_inbox.values():
            session.merge(row)
        for row in self._pending_observations.values():
            session.merge(row)
        session.commit()
        self._session = None
        self._pending_inbox = None
        self._pending_observations = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending_inbox = None
        self._pending_observations = None

    def try_insert_received(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        event_type: str,
        event_version: int,
        handler_version: str,
        processing_owner: str,
        processing_lease_until: datetime,
        received_at: datetime,
        correlation_id: UUID | None,
        jetstream_stream: str | None,
        jetstream_seq: int | None,
        nats_msg_id: str | None,
    ) -> InboxRow | None:
        working = self._working_inbox()
        key = (consumer_name, event_id)
        if key in working:
            return None
        row = IntegrationInboxRow(
            id=uuid4(),
            consumer_name=consumer_name,
            event_id=event_id,
            event_type=event_type,
            event_version=event_version,
            status=InboxStatus.PROCESSING.value,
            processing_owner=processing_owner,
            processing_lease_until=processing_lease_until,
            handler_version=handler_version,
            attempt_count=1,
            first_received_at=received_at,
            last_received_at=received_at,
            processed_at=None,
            quarantined_at=None,
            last_error_code=None,
            last_error_message=None,
            jetstream_stream=jetstream_stream,
            jetstream_seq=jetstream_seq,
            correlation_id=correlation_id,
            nats_msg_id=nats_msg_id,
            processing_started_at=received_at,
        )
        working[key] = row
        return _inbox_from_row(row)

    def load_existing(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None:
        working = self._working_inbox()
        row = working.get((consumer_name, event_id))
        if row is not None:
            return _inbox_from_row(row)
        if self._session is None:
            with self.session_factory() as session:
                existing = session.execute(
                    select(IntegrationInboxRow).where(
                        IntegrationInboxRow.consumer_name == consumer_name,
                        IntegrationInboxRow.event_id == event_id,
                    )
                ).scalar_one_or_none()
                return _inbox_from_row(existing) if existing is not None else None
        existing = self._session.execute(
            select(IntegrationInboxRow).where(
                IntegrationInboxRow.consumer_name == consumer_name,
                IntegrationInboxRow.event_id == event_id,
            )
        ).scalar_one_or_none()
        return _inbox_from_row(existing) if existing is not None else None

    def reclaim_processing(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processing_owner: str,
        processing_lease_until: datetime,
        now: datetime,
    ) -> InboxRow:
        row = self._working_inbox()[(consumer_name, event_id)]
        row.status = InboxStatus.PROCESSING.value
        row.processing_owner = processing_owner
        row.processing_lease_until = processing_lease_until
        row.attempt_count += 1
        row.last_received_at = now
        row.processing_started_at = now
        return _inbox_from_row(row)

    def mark_processed(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processed_at: datetime,
    ) -> None:
        row = self._working_inbox()[(consumer_name, event_id)]
        row.status = InboxStatus.PROCESSED.value
        row.processed_at = processed_at
        row.processing_owner = None
        row.processing_lease_until = None

    def mark_quarantined(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        quarantined_at: datetime,
        error_code: str,
        error_message: str,
    ) -> InboxRow:
        working = self._working_inbox()
        key = (consumer_name, event_id)
        row = working.get(key)
        if row is None:
            row = IntegrationInboxRow(
                id=uuid4(),
                consumer_name=consumer_name,
                event_id=event_id,
                event_type="legacy_bridge.observation.audit_entry",
                event_version=1,
                status=InboxStatus.QUARANTINED.value,
                processing_owner=None,
                processing_lease_until=None,
                handler_version="poison",
                attempt_count=1,
                first_received_at=quarantined_at,
                last_received_at=quarantined_at,
                processed_at=None,
                quarantined_at=quarantined_at,
                last_error_code=error_code,
                last_error_message=sanitize_error_message(error_message),
                jetstream_stream=None,
                jetstream_seq=None,
                correlation_id=None,
                nats_msg_id=None,
                processing_started_at=quarantined_at,
            )
            working[key] = row
        else:
            row.status = InboxStatus.QUARANTINED.value
            row.quarantined_at = quarantined_at
            row.last_error_code = error_code
            row.last_error_message = sanitize_error_message(error_message)
            row.processing_owner = None
            row.processing_lease_until = None
        return _inbox_from_row(row)

    def insert_if_absent(self, observation: LegacyAuditObservation) -> bool:
        working = self._working_observations()
        if observation.event_id in working:
            return False
        row = LegacyAuditObservationRow(
            event_id=observation.event_id,
            source_system=observation.source_system,
            source_table=observation.source_table,
            source_pk=observation.source_pk,
            source_position=observation.source_position,
            source_module=observation.source_module,
            audit_entry_id=observation.audit_entry_id,
            action=observation.action,
            entity_type=observation.entity_type,
            entity_id=observation.entity_id,
            actor_type=observation.actor_type,
            actor_id=observation.actor_id,
            source=observation.source,
            occurred_at=observation.occurred_at,
            bridge_mapper_version=observation.bridge_mapper_version,
            safe_metadata=observation.safe_metadata,
            received_at=observation.received_at,
        )
        working[observation.event_id] = row
        return True

    def get_by_event_id(self, event_id: UUID) -> LegacyAuditObservation | None:
        return SqlAlchemyObservationQuery(self.session_factory).get_by_event_id(event_id)

    def get_by_audit_entry_id(self, audit_entry_id: UUID) -> LegacyAuditObservation | None:
        return SqlAlchemyObservationQuery(self.session_factory).get_by_audit_entry_id(
            audit_entry_id
        )

    def list_by_entity(
        self, *, entity_type: str, entity_id: UUID
    ) -> list[LegacyAuditObservation]:
        return SqlAlchemyObservationQuery(self.session_factory).list_by_entity(
            entity_type=entity_type,
            entity_id=entity_id,
        )

    def list_by_occurred_range(
        self, *, start: datetime, end: datetime
    ) -> list[LegacyAuditObservation]:
        return SqlAlchemyObservationQuery(self.session_factory).list_by_occurred_range(
            start=start,
            end=end,
        )

    def observation_count(self) -> int:
        with self.session_factory() as session:
            return len(session.execute(select(LegacyAuditObservationRow.event_id)).all())

    def _working_inbox(self) -> dict[tuple[str, UUID], IntegrationInboxRow]:
        if self._pending_inbox is None:
            msg = "inbox mutation outside transaction"
            raise RuntimeError(msg)
        return self._pending_inbox

    def _working_observations(self) -> dict[UUID, LegacyAuditObservationRow]:
        if self._pending_observations is None:
            msg = "observation mutation outside transaction"
            raise RuntimeError(msg)
        return self._pending_observations


@dataclass(frozen=True, slots=True)
class SqlAlchemyObservationQuery:
    """Read-only query adapter for Legacy Audit observations."""

    session_factory: sessionmaker[Session]

    def get_by_event_id(self, event_id: UUID) -> LegacyAuditObservation | None:
        with self.session_factory() as session:
            row = session.get(LegacyAuditObservationRow, event_id)
            return _observation_from_row(row) if row is not None else None

    def get_by_audit_entry_id(self, audit_entry_id: UUID) -> LegacyAuditObservation | None:
        with self.session_factory() as session:
            row = session.execute(
                select(LegacyAuditObservationRow).where(
                    LegacyAuditObservationRow.audit_entry_id == audit_entry_id
                )
            ).scalar_one_or_none()
            return _observation_from_row(row) if row is not None else None

    def list_by_entity(
        self, *, entity_type: str, entity_id: UUID
    ) -> list[LegacyAuditObservation]:
        with self.session_factory() as session:
            rows = session.execute(
                select(LegacyAuditObservationRow).where(
                    LegacyAuditObservationRow.entity_type == entity_type,
                    LegacyAuditObservationRow.entity_id == entity_id,
                )
            ).scalars()
            return [_observation_from_row(row) for row in rows]

    def list_by_occurred_range(
        self, *, start: datetime, end: datetime
    ) -> list[LegacyAuditObservation]:
        with self.session_factory() as session:
            rows = session.execute(
                select(LegacyAuditObservationRow).where(
                    LegacyAuditObservationRow.occurred_at >= start,
                    LegacyAuditObservationRow.occurred_at <= end,
                )
            ).scalars()
            return [_observation_from_row(row) for row in rows]


def _inbox_from_row(row: IntegrationInboxRow) -> InboxRow:
    return InboxRow(
        id=row.id,  # type: ignore[arg-type]
        consumer_name=row.consumer_name,
        event_id=row.event_id,  # type: ignore[arg-type]
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        processing_owner=row.processing_owner,
        processing_lease_until=row.processing_lease_until,  # type: ignore[arg-type]
        handler_version=row.handler_version,
        attempt_count=row.attempt_count,
        first_received_at=row.first_received_at,  # type: ignore[arg-type]
        last_received_at=row.last_received_at,  # type: ignore[arg-type]
        processed_at=row.processed_at,  # type: ignore[arg-type]
        quarantined_at=row.quarantined_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        jetstream_stream=row.jetstream_stream,
        jetstream_seq=row.jetstream_seq,
        correlation_id=row.correlation_id,  # type: ignore[arg-type]
        nats_msg_id=row.nats_msg_id,
        processing_started_at=row.processing_started_at,  # type: ignore[arg-type]
    )


def _observation_from_row(row: LegacyAuditObservationRow) -> LegacyAuditObservation:
    return LegacyAuditObservation(
        event_id=row.event_id,  # type: ignore[arg-type]
        source_system=row.source_system,
        source_table=row.source_table,
        source_pk=row.source_pk,  # type: ignore[arg-type]
        source_position=row.source_position,
        source_module=row.source_module,
        audit_entry_id=row.audit_entry_id,  # type: ignore[arg-type]
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,  # type: ignore[arg-type]
        actor_type=row.actor_type,
        actor_id=row.actor_id,  # type: ignore[arg-type]
        source=row.source,
        occurred_at=row.occurred_at,  # type: ignore[arg-type]
        bridge_mapper_version=row.bridge_mapper_version,
        safe_metadata=dict(row.safe_metadata),
        received_at=row.received_at,  # type: ignore[arg-type]
    )
