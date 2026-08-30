"""Shared test helpers for envelope examples."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from event_envelope import (
    AggregateScope,
    DataClassification,
    EventEnvelope,
    MessageKind,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ENVELOPE_CONTRACT_DIR = REPO_ROOT / "contracts" / "events" / "envelope"
FIXTURES_DIR = ENVELOPE_CONTRACT_DIR / "examples"
SCHEMA_PATH = ENVELOPE_CONTRACT_DIR / "v1.schema.json"

EVENT_ID = UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")
CORRELATION_ID = UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
CAUSATION_ID = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
AGGREGATE_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
TENANT_ID = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
OCCURRED_AT = datetime(2026, 8, 30, 11, 15, 42, 456000, tzinfo=UTC)


def load_example(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def aggregate_command() -> EventEnvelope:
    return EventEnvelope(
        event_id=EVENT_ID,
        event_type="delivery.command.complete",
        event_version=1,
        occurred_at=OCCURRED_AT,
        producer="delivery",
        message_kind=MessageKind.COMMAND,
        aggregate_scope=AggregateScope.AGGREGATE,
        aggregate_type="shipment",
        aggregate_id=AGGREGATE_ID,
        aggregate_version=7,
        correlation_id=CORRELATION_ID,
        causation_id=CAUSATION_ID,
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        tenant_id=TENANT_ID,
        data_classification=DataClassification.INTERNAL,
        pii_present=False,
        payload={"task_id": "2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c", "expected_version": 7},
        metadata={"actor_type": "service"},
    )


def aggregate_integration_event() -> EventEnvelope:
    return EventEnvelope(
        event_id=EVENT_ID,
        event_type="pickup.fact.accepted",
        event_version=1,
        occurred_at=OCCURRED_AT,
        producer="pickup",
        message_kind=MessageKind.INTEGRATION,
        aggregate_scope=AggregateScope.AGGREGATE,
        aggregate_type="shipment",
        aggregate_id=AGGREGATE_ID,
        aggregate_version=4,
        correlation_id=CORRELATION_ID,
        causation_id=CAUSATION_ID,
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        tenant_id=TENANT_ID,
        data_classification=DataClassification.INTERNAL,
        pii_present=False,
        payload={"pickup_task_id": "2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c"},
    )


def non_aggregate_platform_message() -> EventEnvelope:
    return EventEnvelope(
        event_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        event_type="platform.notification.config_changed",
        event_version=1,
        occurred_at=datetime(2026, 8, 30, 14, 32, 1, 123000, tzinfo=UTC),
        producer="notification",
        message_kind=MessageKind.PROJECTION,
        aggregate_scope=AggregateScope.NON_AGGREGATE,
        correlation_id=CAUSATION_ID,
        data_classification=DataClassification.PUBLIC,
        pii_present=False,
        payload={"config_key": "push_retry_backoff_seconds", "new_value": 30},
    )
