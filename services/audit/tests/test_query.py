"""Observation query-port tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from messaging_conformance.observation import append_only_observation_event_id

from audit.application.coordinator import ObservationConsumerCoordinator
from audit.domain.contract import A2_EVENT_ID_NAMESPACE
from audit.domain.types import Delivery
from audit.infrastructure.memory import MemoryAuditStore

AUDIT_PK = UUID("8d2e1f0a-9b8c-7d6e-5f4a-3b2c1d0e9f8a")
ENTITY_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def test_query_by_event_id_and_audit_entry(
    coordinator: ObservationConsumerCoordinator,
    store: MemoryAuditStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    coordinator.handle(make_delivery())
    event_id = append_only_observation_event_id(
        A2_EVENT_ID_NAMESPACE,
        source_system="legacy",
        source_table="audit_logs",
        source_pk=str(AUDIT_PK),
    )
    by_event = store.get_by_event_id(event_id)
    by_entry = store.get_by_audit_entry_id(AUDIT_PK)
    assert by_event is not None
    assert by_entry is not None
    assert by_event.event_id == by_entry.event_id


def test_query_by_entity_and_occurred_range(
    coordinator: ObservationConsumerCoordinator,
    store: MemoryAuditStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    coordinator.handle(make_delivery())
    other_pk = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    other_event_id = append_only_observation_event_id(
        A2_EVENT_ID_NAMESPACE,
        source_system="legacy",
        source_table="audit_logs",
        source_pk=str(other_pk),
    )
    envelope = make_envelope(
        source_pk=other_pk,
        payload=make_payload(
            source_pk=other_pk,
            audit_entry_id=str(other_pk),
            entity_type="order",
            entity_id="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            occurred_at="2026-08-31T10:00:00.000Z",
        ),
        event_id=str(other_event_id),
    )
    coordinator.handle(make_delivery(envelope))

    matched = store.list_by_entity(entity_type="shipment", entity_id=ENTITY_ID)
    assert len(matched) == 1
    assert matched[0].audit_entry_id == AUDIT_PK

    window = store.list_by_occurred_range(
        start=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        end=datetime(2026, 8, 30, 23, 59, tzinfo=UTC),
    )
    assert len(window) == 1
    later = store.list_by_occurred_range(
        start=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        end=datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
    )
    assert len(later) == 1
    assert later[0].entity_type == "order"
