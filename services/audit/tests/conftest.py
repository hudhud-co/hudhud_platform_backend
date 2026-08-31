"""Shared fixtures for Audit A2 consumer tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from messaging_conformance.observation import append_only_observation_event_id

from audit.application.coordinator import ObservationConsumerCoordinator
from audit.domain.contract import (
    A2_DURABLE_CONSUMER,
    A2_EVENT_ID_NAMESPACE,
    A2_EVENT_TYPE,
    A2_SOURCE_SYSTEM,
    A2_SOURCE_TABLE,
    A2_STREAM,
    A2_SUBJECT,
)
from audit.domain.types import Delivery
from audit.infrastructure.memory import MemoryAuditStore, RecordingTransport

AUDIT_PK = UUID("8d2e1f0a-9b8c-7d6e-5f4a-3b2c1d0e9f8a")
ENTITY_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
ACTOR_ID = UUID("c0ffee00-0000-4000-8000-000000000001")
CORRELATION_ID = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
OCCURRED_AT = "2026-08-30T14:32:01.123Z"


def a2_event_id(*, source_pk: UUID = AUDIT_PK) -> UUID:
    return append_only_observation_event_id(
        A2_EVENT_ID_NAMESPACE,
        source_system=A2_SOURCE_SYSTEM,
        source_table=A2_SOURCE_TABLE,
        source_pk=str(source_pk),
    )


def valid_a2_payload(*, source_pk: UUID = AUDIT_PK, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_table": A2_SOURCE_TABLE,
        "source_pk": str(source_pk),
        "source_position": "0/16B3A8D8",
        "source_module": "delivery_task",
        "audit_entry_id": str(source_pk),
        "action": "SHIPMENT_DELIVERED",
        "entity_type": "shipment",
        "entity_id": str(ENTITY_ID),
        "actor_type": "driver",
        "actor_id": str(ACTOR_ID),
        "source": "delivery_task",
        "occurred_at": OCCURRED_AT,
        "metadata": {"delivery_task_id": "8e7d6c5b-4a39-4120-9f8e-1a2b3c4d5e6f"},
        "bridge_mapper_version": "1.0.0",
    }
    payload.update(overrides)
    return payload


def valid_a2_envelope(*, source_pk: UUID = AUDIT_PK, **overrides: Any) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "envelope_version": 1,
        "event_id": str(a2_event_id(source_pk=source_pk)),
        "event_type": A2_EVENT_TYPE,
        "event_version": 1,
        "occurred_at": OCCURRED_AT,
        "producer": "legacy_bridge",
        "message_kind": "integration",
        "aggregate_scope": "non_aggregate",
        "correlation_id": str(CORRELATION_ID),
        "data_classification": "internal",
        "pii_present": False,
        "payload": valid_a2_payload(source_pk=source_pk),
    }
    if "payload" in overrides:
        envelope["payload"] = overrides.pop("payload")
    envelope.update(overrides)
    return envelope


def a2_delivery(
    envelope: dict[str, Any] | None = None,
    *,
    subject: str = A2_SUBJECT,
    stream: str = A2_STREAM,
    consumer_name: str = A2_DURABLE_CONSUMER,
    nats_msg_id: str | None = None,
    jetstream_seq: int | None = 11,
) -> Delivery:
    body = json.dumps(envelope or valid_a2_envelope()).encode("utf-8")
    return Delivery(
        body=body,
        subject=subject,
        stream=stream,
        consumer_name=consumer_name,
        nats_msg_id=nats_msg_id,
        jetstream_seq=jetstream_seq,
    )


@pytest.fixture
def store() -> MemoryAuditStore:
    return MemoryAuditStore()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 30, 14, 32, 2, tzinfo=UTC)


@pytest.fixture
def coordinator(store: MemoryAuditStore, now: datetime) -> ObservationConsumerCoordinator:
    return ObservationConsumerCoordinator(
        unit_of_work=store,
        inbox=store,
        observations=store,
        transport=RecordingTransport(store),
        consumer_name=A2_DURABLE_CONSUMER,
        handler_version="0.1.0",
        processing_owner="test-worker",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=lambda: now,
    )


@pytest.fixture
def make_delivery() -> Callable[..., Delivery]:
    return a2_delivery


@pytest.fixture
def make_envelope() -> Callable[..., dict[str, Any]]:
    return valid_a2_envelope


@pytest.fixture
def make_payload() -> Callable[..., dict[str, Any]]:
    return valid_a2_payload
