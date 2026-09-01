"""In-memory inbox, timeline store, unit of work, and recording transport."""

from __future__ import annotations

import copy
from uuid import UUID, uuid4

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from tracking.domain.errors import RetryableHandlerError
from tracking.domain.sanitize import sanitize_error_message
from tracking.domain.types import (
    Delivery,
    InboxRow,
    ShipmentTimelineEntry,
    TimelinePage,
    TimelinePageCursor,
)
from tracking.infrastructure.persistence.sqlalchemy_store import SqlAlchemyTimelineQuery


class RecordingTransport:
    """Records ACK/NAK/DEFER in the same action log as unit-of-work commits."""

    def __init__(self, store: MemoryTrackingStore) -> None:
        self._store = store

    def ack(self, delivery: Delivery) -> None:
        if self._store.crash_before_ack:
            self._store.crash_before_ack = False
            raise SimulatedCrash("crash after commit before ack")
        self._store.actions.append("ack")
        self._store.transport_actions.append(JetStreamConsumerAction.ACK)
        self._store.last_delivery = delivery

    def nak(self, delivery: Delivery) -> None:
        self._store.actions.append("nak")
        self._store.transport_actions.append(JetStreamConsumerAction.NAK)
        self._store.last_delivery = delivery

    def defer(self, delivery: Delivery) -> None:
        self._store.actions.append("defer")
        self._store.transport_actions.append(JetStreamConsumerAction.DEFER)
        self._store.last_delivery = delivery


class SimulatedCrash(RuntimeError):
    """Test hook: commit succeeded, ACK not yet sent."""


class MemoryTrackingStore:
    """Copy-on-write in-memory persistence for coordinator tests."""

    def __init__(self) -> None:
        self._inbox: dict[tuple[str, UUID], InboxRow] = {}
        self._timeline_entries: dict[UUID, ShipmentTimelineEntry] = {}
        self._tx_inbox: dict[tuple[str, UUID], InboxRow] | None = None
        self._tx_timeline_entries: dict[UUID, ShipmentTimelineEntry] | None = None
        self.actions: list[str] = []
        self.transport_actions: list[JetStreamConsumerAction] = []
        self.last_delivery: Delivery | None = None
        self.fail_next_projection = False
        self.fail_next_quarantine = False
        self.crash_before_ack = False

    def begin(self) -> None:
        self._tx_inbox = copy.deepcopy(self._inbox)
        self._tx_timeline_entries = copy.deepcopy(self._timeline_entries)

    def commit(self) -> None:
        if self._tx_inbox is None or self._tx_timeline_entries is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._inbox = self._tx_inbox
        self._timeline_entries = self._tx_timeline_entries
        self._tx_inbox = None
        self._tx_timeline_entries = None
        self.actions.append("commit")

    def rollback(self) -> None:
        self._tx_inbox = None
        self._tx_timeline_entries = None
        self.actions.append("rollback")

    def seed_inbox(self, row: InboxRow) -> None:
        self._inbox[(row.consumer_name, row.event_id)] = copy.deepcopy(row)

    def seed_timeline_entry(self, entry: ShipmentTimelineEntry) -> None:
        self._timeline_entries[entry.event_id] = entry

    def try_insert_received(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        event_type: str,
        event_version: int,
        handler_version: str,
        processing_owner: str,
        processing_lease_until: object,
        received_at: object,
        correlation_id: object,
        jetstream_stream: str | None,
        jetstream_seq: int | None,
        nats_msg_id: str | None,
    ) -> InboxRow | None:
        working = self._working_inbox()
        key = (consumer_name, event_id)
        if key in working:
            return None
        row = InboxRow(
            id=uuid4(),
            consumer_name=consumer_name,
            event_id=event_id,
            event_type=event_type,
            event_version=event_version,
            status=InboxStatus.PROCESSING,
            processing_owner=processing_owner,
            processing_lease_until=processing_lease_until,  # type: ignore[arg-type]
            handler_version=handler_version,
            attempt_count=1,
            first_received_at=received_at,  # type: ignore[arg-type]
            last_received_at=received_at,  # type: ignore[arg-type]
            processed_at=None,
            quarantined_at=None,
            last_error_code=None,
            last_error_message=None,
            jetstream_stream=jetstream_stream,
            jetstream_seq=jetstream_seq,
            correlation_id=correlation_id,  # type: ignore[arg-type]
            nats_msg_id=nats_msg_id,
            processing_started_at=received_at,  # type: ignore[arg-type]
        )
        working[key] = row
        return copy.deepcopy(row)

    def load_existing(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None:
        working = self._working_inbox()
        row = working.get((consumer_name, event_id))
        return copy.deepcopy(row) if row is not None else None

    def reclaim_processing(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processing_owner: str,
        processing_lease_until: object,
        now: object,
    ) -> InboxRow:
        working = self._working_inbox()
        row = working[(consumer_name, event_id)]
        row.status = InboxStatus.PROCESSING
        row.processing_owner = processing_owner
        row.processing_lease_until = processing_lease_until  # type: ignore[assignment]
        row.attempt_count += 1
        row.last_received_at = now  # type: ignore[assignment]
        row.processing_started_at = now  # type: ignore[assignment]
        return copy.deepcopy(row)

    def mark_processed(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processed_at: object,
    ) -> None:
        row = self._working_inbox()[(consumer_name, event_id)]
        row.status = InboxStatus.PROCESSED
        row.processed_at = processed_at  # type: ignore[assignment]
        row.processing_owner = None
        row.processing_lease_until = None

    def mark_quarantined(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        quarantined_at: object,
        error_code: str,
        error_message: str,
    ) -> InboxRow:
        if self.fail_next_quarantine:
            self.fail_next_quarantine = False
            raise RetryableHandlerError("DB_DEADLOCK", "injected quarantine persistence failure")
        working = self._working_inbox()
        key = (consumer_name, event_id)
        row = working[key]
        row.status = InboxStatus.QUARANTINED
        row.quarantined_at = quarantined_at  # type: ignore[assignment]
        row.last_error_code = error_code
        row.last_error_message = sanitize_error_message(error_message)
        row.processing_owner = None
        row.processing_lease_until = None
        return copy.deepcopy(row)

    def insert_if_absent(self, entry: ShipmentTimelineEntry) -> bool:
        if self.fail_next_projection:
            self.fail_next_projection = False
            raise RetryableHandlerError("DB_DEADLOCK", "injected projection failure")
        working = self._working_timeline_entries()
        if entry.event_id in working:
            return False
        working[entry.event_id] = entry
        return True

    def get_by_event_id(self, event_id: UUID) -> ShipmentTimelineEntry | None:
        return self._timeline_entries.get(event_id)

    def list_by_shipment_id(
        self,
        *,
        shipment_id: UUID,
        cursor: TimelinePageCursor | None = None,
        page_size: int,
    ) -> TimelinePage:
        entries = [
            item
            for item in self._timeline_entries.values()
            if item.shipment_id == shipment_id
        ]
        entries.sort(key=lambda item: (item.occurred_at, item.event_id))
        if cursor is not None:
            entries = [
                item
                for item in entries
                if (item.occurred_at, item.event_id) > (cursor.occurred_at, cursor.event_id)
            ]
        page = entries[: page_size + 1]
        next_cursor = None
        if len(page) > page_size:
            last = page[page_size - 1]
            next_cursor = TimelinePageCursor(occurred_at=last.occurred_at, event_id=last.event_id)
            page = page[:page_size]
        return TimelinePage(entries=tuple(page), next_cursor=next_cursor)

    def timeline_entry_count(self) -> int:
        return len(self._timeline_entries)

    def committed_inbox(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None:
        row = self._inbox.get((consumer_name, event_id))
        return copy.deepcopy(row) if row is not None else None

    def _working_inbox(self) -> dict[tuple[str, UUID], InboxRow]:
        if self._tx_inbox is None:
            msg = "inbox mutation outside transaction"
            raise RuntimeError(msg)
        return self._tx_inbox

    def _working_timeline_entries(self) -> dict[UUID, ShipmentTimelineEntry]:
        if self._tx_timeline_entries is None:
            msg = "timeline mutation outside transaction"
            raise RuntimeError(msg)
        return self._tx_timeline_entries


class MemoryTimelineQuery(SqlAlchemyTimelineQuery):
    """Compatibility shim — memory store implements query port directly."""

    def __init__(self, store: MemoryTrackingStore) -> None:  # type: ignore[super-init-not-called]
        self._store = store

    def get_by_event_id(self, event_id: UUID) -> ShipmentTimelineEntry | None:
        return self._store.get_by_event_id(event_id)

    def list_by_shipment_id(
        self,
        *,
        shipment_id: UUID,
        cursor: TimelinePageCursor | None = None,
        page_size: int,
    ) -> TimelinePage:
        return self._store.list_by_shipment_id(
            shipment_id=shipment_id,
            cursor=cursor,
            page_size=page_size,
        )
