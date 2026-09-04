"""Synchronous PostgreSQL adapter for native pickup.fact.accepted consumption."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from messaging_conformance.enums import InboxStatus
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from shipment.domain.entities import (
    AcceptanceDecisionRecord,
    AuditLogEntry,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.errors import RetryableHandlerError
from shipment.domain.sanitize import sanitize_error_message
from shipment.domain.types import InboxRow
from shipment.infrastructure.persistence.mappers import (
    audit_log_from_row,
    audit_log_to_row,
    decision_from_row,
    decision_to_row,
    inbox_from_row,
    shipment_event_from_row,
    shipment_event_to_row,
    shipment_from_row,
    shipment_to_row,
)
from shipment.infrastructure.persistence.models import (
    AcceptanceAuditLogRow,
    AcceptanceDecisionRow,
    IntegrationInboxRow,
    ShipmentEventRow,
    ShipmentRow,
)


@dataclass
class SqlAlchemyAcceptedFactStore:
    """Atomic native-fact UoW + ADR-0008 inbox — Shipment-owned PostgreSQL only."""

    session_factory: sessionmaker[Session]
    _session: Session | None = field(default=None, init=False, repr=False)
    _shipment_versions: dict[UUID, int] = field(default_factory=dict, init=False, repr=False)

    @property
    def shipments(self) -> _ShipmentRepo:
        return _ShipmentRepo(self)

    @property
    def shipment_events(self) -> _ShipmentEventRepo:
        return _ShipmentEventRepo(self)

    @property
    def audit_logs(self) -> _AuditLogRepo:
        return _AuditLogRepo(self)

    @property
    def acceptance_decisions(self) -> _DecisionRepo:
        return _DecisionRepo(self)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self.session_factory()
        self._shipment_versions.clear()

    def commit(self) -> None:
        session = self._require_session()
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise RetryableHandlerError(
                "DB_INTEGRITY_ERROR",
                "inbox or domain uniqueness conflict",
            ) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._clear()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._clear()

    def persist_created_shipment(self, shipment: Shipment) -> None:
        """Seed a CREATED shipment outside the consumer transaction."""
        if self._session is not None:
            msg = "seed shipment during consumer transaction"
            raise RuntimeError(msg)
        with self.session_factory() as session:
            session.add(shipment_to_row(shipment, version=shipment.version))
            session.commit()

    def persist_decision(self, decision: AcceptanceDecisionRecord) -> None:
        """Seed a prior HTTP/compatibility acceptance decision outside the consumer tx."""
        if self._session is not None:
            msg = "seed decision during consumer transaction"
            raise RuntimeError(msg)
        with self.session_factory() as session:
            session.add(decision_to_row(decision))
            session.commit()

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
        aggregate_type: str | None = None,
        aggregate_id: UUID | None = None,
        aggregate_version: int | None = None,
    ) -> InboxRow | None:
        session = self._require_session()
        existing = self._load_inbox_row(consumer_name=consumer_name, event_id=event_id)
        if existing is not None:
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
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
        )
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            return None
        return inbox_from_row(row)

    def load_existing(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None:
        session, owned = self._session_or_factory()
        try:
            row = session.execute(
                select(IntegrationInboxRow).where(
                    IntegrationInboxRow.consumer_name == consumer_name,
                    IntegrationInboxRow.event_id == event_id,
                )
            ).scalar_one_or_none()
            return inbox_from_row(row) if row is not None else None
        finally:
            if owned:
                session.close()

    def reclaim_processing(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processing_owner: str,
        processing_lease_until: datetime,
        now: datetime,
    ) -> InboxRow:
        row = self._require_inbox_row(consumer_name=consumer_name, event_id=event_id)
        row.status = InboxStatus.PROCESSING.value
        row.processing_owner = processing_owner
        row.processing_lease_until = processing_lease_until
        row.attempt_count += 1
        row.last_received_at = now
        row.processing_started_at = now
        return inbox_from_row(row)

    def mark_processed(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processed_at: datetime,
    ) -> None:
        row = self._require_inbox_row(consumer_name=consumer_name, event_id=event_id)
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
        row = self._load_inbox_row(consumer_name=consumer_name, event_id=event_id)
        sanitized = sanitize_error_message(error_message)
        if row is None:
            session = self._require_session()
            row = IntegrationInboxRow(
                id=uuid4(),
                consumer_name=consumer_name,
                event_id=event_id,
                event_type="pickup.fact.accepted",
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
                last_error_message=sanitized,
                jetstream_stream=None,
                jetstream_seq=None,
                correlation_id=None,
                nats_msg_id=None,
                processing_started_at=quarantined_at,
            )
            session.add(row)
        else:
            row.status = InboxStatus.QUARANTINED.value
            row.quarantined_at = quarantined_at
            row.last_error_code = error_code
            row.last_error_message = sanitized
            row.processing_owner = None
            row.processing_lease_until = None
        return inbox_from_row(row)

    def _require_session(self) -> Session:
        if self._session is None:
            msg = "inbox/domain mutation outside transaction"
            raise RuntimeError(msg)
        return self._session

    def _session_or_factory(self) -> tuple[Session, bool]:
        if self._session is not None:
            return self._session, False
        return self.session_factory(), True

    def _clear(self) -> None:
        self._session = None
        self._shipment_versions.clear()

    def _load_inbox_row(
        self, *, consumer_name: str, event_id: UUID
    ) -> IntegrationInboxRow | None:
        session = self._require_session()
        return session.execute(
            select(IntegrationInboxRow).where(
                IntegrationInboxRow.consumer_name == consumer_name,
                IntegrationInboxRow.event_id == event_id,
            )
        ).scalar_one_or_none()

    def _require_inbox_row(self, *, consumer_name: str, event_id: UUID) -> IntegrationInboxRow:
        row = self._load_inbox_row(consumer_name=consumer_name, event_id=event_id)
        if row is None:
            msg = f"missing inbox row {(consumer_name, event_id)}"
            raise RuntimeError(msg)
        return row


class _ShipmentRepo:
    def __init__(self, store: SqlAlchemyAcceptedFactStore) -> None:
        self._store = store

    def save_shipment(self, shipment: Shipment) -> None:
        session = self._store._require_session()
        expected_version = self._store._shipment_versions.get(shipment.shipment_id)
        if expected_version is None:
            existing = session.get(ShipmentRow, shipment.shipment_id)
            if existing is None:
                session.add(shipment_to_row(shipment, version=shipment.version))
                self._store._shipment_versions[shipment.shipment_id] = shipment.version
                return
            expected_version = existing.version
        row = shipment_to_row(shipment, version=shipment.version)
        result = session.execute(
            update(ShipmentRow)
            .where(
                ShipmentRow.shipment_id == shipment.shipment_id,
                ShipmentRow.version == expected_version,
            )
            .values(
                current_status=row.current_status,
                accepted_at=row.accepted_at,
                sla_started_at=row.sla_started_at,
                current_custody_type=row.current_custody_type,
                current_custody_id=row.current_custody_id,
                version=shipment.version,
            )
        )
        if result.rowcount != 1:
            raise RetryableHandlerError(
                "DB_SERIALIZATION_FAILURE",
                "stale shipment version during native fact apply",
            )
        self._store._shipment_versions[shipment.shipment_id] = shipment.version

    def get_shipment(self, shipment_id: UUID) -> Shipment | None:
        session, owned = self._store._session_or_factory()
        try:
            row = session.get(ShipmentRow, shipment_id)
            if row is None:
                return None
            shipment, version = shipment_from_row(row)
            self._store._shipment_versions[shipment_id] = version
            return shipment
        finally:
            if owned:
                session.close()


class _ShipmentEventRepo:
    def __init__(self, store: SqlAlchemyAcceptedFactStore) -> None:
        self._store = store

    def append_event(self, event: ShipmentEvent) -> None:
        session = self._store._require_session()
        session.add(shipment_event_to_row(event))

    def list_events_for_shipment(self, shipment_id: UUID) -> tuple[ShipmentEvent, ...]:
        session, owned = self._store._session_or_factory()
        try:
            rows = session.execute(
                select(ShipmentEventRow)
                .where(ShipmentEventRow.shipment_id == shipment_id)
                .order_by(ShipmentEventRow.occurred_at)
            ).scalars()
            return tuple(shipment_event_from_row(row) for row in rows)
        finally:
            if owned:
                session.close()


class _AuditLogRepo:
    def __init__(self, store: SqlAlchemyAcceptedFactStore) -> None:
        self._store = store

    def append_entry(self, entry: AuditLogEntry) -> None:
        session = self._store._require_session()
        session.add(audit_log_to_row(entry))

    def list_entries_for_entity(
        self, entity_type: str, entity_id: str
    ) -> tuple[AuditLogEntry, ...]:
        session, owned = self._store._session_or_factory()
        try:
            rows = session.execute(
                select(AcceptanceAuditLogRow)
                .where(
                    AcceptanceAuditLogRow.entity_type == entity_type,
                    AcceptanceAuditLogRow.entity_id == entity_id,
                )
                .order_by(AcceptanceAuditLogRow.occurred_at)
            ).scalars()
            return tuple(audit_log_from_row(row) for row in rows)
        finally:
            if owned:
                session.close()


class _DecisionRepo:
    def __init__(self, store: SqlAlchemyAcceptedFactStore) -> None:
        self._store = store

    def save(self, decision: AcceptanceDecisionRecord) -> None:
        session = self._store._require_session()
        session.add(decision_to_row(decision))

    def get_for_shipment(self, shipment_id: UUID) -> AcceptanceDecisionRecord | None:
        session, owned = self._store._session_or_factory()
        try:
            row = session.execute(
                select(AcceptanceDecisionRow).where(
                    AcceptanceDecisionRow.shipment_id == shipment_id
                )
            ).scalar_one_or_none()
            return decision_from_row(row) if row is not None else None
        finally:
            if owned:
                session.close()

    def get_for_pickup_task(self, pickup_task_id: UUID) -> AcceptanceDecisionRecord | None:
        session, owned = self._store._session_or_factory()
        try:
            row = session.execute(
                select(AcceptanceDecisionRow).where(
                    AcceptanceDecisionRow.pickup_task_id == pickup_task_id
                )
            ).scalar_one_or_none()
            return decision_from_row(row) if row is not None else None
        finally:
            if owned:
                session.close()
