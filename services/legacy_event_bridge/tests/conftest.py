"""Shared test fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from legacy_event_bridge.application.landing import (
    LandingCoordinator,
    ReplicationFeedbackCoordinator,
)
from legacy_event_bridge.application.mapping import MappingService
from legacy_event_bridge.application.pipeline import BridgePipeline
from legacy_event_bridge.application.publisher import OutboxPublisher
from legacy_event_bridge.domain.types import CdcChange
from legacy_event_bridge.infrastructure.memory import FixtureCdcAdapter, MemoryBridgeStore

CAPTURE_SOURCE = "legacy_publication"
SOURCE_SYSTEM = "legacy"
MAPPER_VERSION = "1.0.0"

SHIPMENT_PK = UUID("2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c")
SHIPMENT_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
AUDIT_PK = UUID("8d2e1f0a-9b8c-7d6e-5f4a-3b2c1d0e9f8a")


@pytest.fixture
def store() -> MemoryBridgeStore:
    return MemoryBridgeStore()


@pytest.fixture
def cdc_adapter() -> FixtureCdcAdapter:
    return FixtureCdcAdapter()


@pytest.fixture
def pipeline(store: MemoryBridgeStore, cdc_adapter: FixtureCdcAdapter) -> BridgePipeline:
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    feedback = ReplicationFeedbackCoordinator(
        checkpoint_store=store,
        feedback_port=store,
    )
    mapping = MappingService(
        landing_store=store,
        outbox_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
        max_attempts=5,
    )
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=store,
        owner_id="test-relay",
        batch_size=50,
        lease_seconds=30,
    )
    return BridgePipeline(
        cdc_adapter=cdc_adapter,
        landing_coordinator=landing,
        feedback_coordinator=feedback,
        mapping_service=mapping,
        outbox_publisher=publisher,
        capture_source=CAPTURE_SOURCE,
    )


def shipment_change(
    *,
    source_pk: UUID = SHIPMENT_PK,
    source_position: str = "0/16B3A8C0",
    legacy_event_type: str = "SHIPMENT_DELIVERED",
) -> CdcChange:
    return CdcChange(
        source_system=SOURCE_SYSTEM,
        source_table="shipment_events",
        source_pk=source_pk,
        source_position=source_position,
        capture_slot=CAPTURE_SOURCE,
        normalized_fields={
            "source_module": "delivery_task",
            "legacy_event_type": legacy_event_type,
            "occurred_at": "2026-08-30T11:15:42.456Z",
            "old_status": "OUT_FOR_DELIVERY",
            "new_status": "DELIVERED",
            "shipment_id": str(SHIPMENT_ID),
            "actor_type": "driver",
            "actor_id": "c0ffee00-0000-4000-8000-000000000001",
            "metadata": {"delivery_task_id": "8e7d6c5b-4a39-4120-9f8e-1a2b3c4d5e6f"},
        },
        received_at=datetime(2026, 8, 30, 11, 15, 43, tzinfo=UTC),
    )


def audit_change(
    *,
    source_pk: UUID = AUDIT_PK,
    source_position: str = "0/16B3A8D8",
) -> CdcChange:
    return CdcChange(
        source_system=SOURCE_SYSTEM,
        source_table="audit_logs",
        source_pk=source_pk,
        source_position=source_position,
        capture_slot=CAPTURE_SOURCE,
        normalized_fields={
            "source_module": "delivery_task",
            "action": "SHIPMENT_DELIVERED",
            "entity_type": "shipment",
            "entity_id": str(SHIPMENT_ID),
            "actor_type": "driver",
            "actor_id": "c0ffee00-0000-4000-8000-000000000001",
            "source": "delivery_task",
            "occurred_at": "2026-08-30T14:32:01.123Z",
            "metadata": {"delivery_task_id": "8e7d6c5b-4a39-4120-9f8e-1a2b3c4d5e6f"},
        },
        received_at=datetime(2026, 8, 30, 14, 32, 2, tzinfo=UTC),
    )
