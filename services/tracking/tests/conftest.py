"""Shared fixtures for Tracking A1 consumer tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from messaging_conformance.observation import append_only_observation_event_id

from tracking.application.coordinator import TimelineConsumerCoordinator
from tracking.domain.contract import (
    A1_DURABLE_CONSUMER,
    A1_EVENT_ID_NAMESPACE,
    A1_EVENT_TYPE,
    A1_SOURCE_SYSTEM,
    A1_SOURCE_TABLE,
    A1_STREAM,
    A1_SUBJECT,
)
from tracking.domain.types import Delivery
from tracking.infrastructure.memory import MemoryTrackingStore, RecordingTransport

SOURCE_PK = UUID("2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c")
SHIPMENT_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
ACTOR_ID = UUID("c0ffee00-0000-4000-8000-000000000001")
CORRELATION_ID = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
OCCURRED_AT = "2026-08-30T11:15:42.456Z"


def a1_event_id(*, source_pk: UUID = SOURCE_PK) -> UUID:
    return append_only_observation_event_id(
        A1_EVENT_ID_NAMESPACE,
        source_system=A1_SOURCE_SYSTEM,
        source_table=A1_SOURCE_TABLE,
        source_pk=str(source_pk),
    )


def valid_a1_payload(*, source_pk: UUID = SOURCE_PK, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_table": A1_SOURCE_TABLE,
        "source_pk": str(source_pk),
        "source_position": f"{OCCURRED_AT}|{source_pk}",
        "source_module": "shipment",
        "legacy_event_type": "SHIPMENT_CREATED",
        "occurred_at": OCCURRED_AT,
        "shipment_id": str(SHIPMENT_ID),
        "old_status": None,
        "new_status": "CREATED",
        "actor_type": "system",
        "actor_id": str(ACTOR_ID),
        "metadata": {"note": "safe"},
        "bridge_mapper_version": "1.0.0",
    }
    payload.update(overrides)
    return payload


def valid_a1_envelope(*, source_pk: UUID = SOURCE_PK, **overrides: Any) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "envelope_version": 1,
        "event_id": str(a1_event_id(source_pk=source_pk)),
        "event_type": A1_EVENT_TYPE,
        "event_version": 1,
        "occurred_at": OCCURRED_AT,
        "producer": "legacy_bridge",
        "message_kind": "integration",
        "aggregate_scope": "non_aggregate",
        "correlation_id": str(CORRELATION_ID),
        "data_classification": "internal",
        "pii_present": False,
        "payload": valid_a1_payload(source_pk=source_pk),
    }
    if "payload" in overrides:
        envelope["payload"] = overrides.pop("payload")
    envelope.update(overrides)
    return envelope


def a1_delivery(
    envelope: dict[str, Any] | None = None,
    *,
    subject: str = A1_SUBJECT,
    stream: str = A1_STREAM,
    consumer_name: str = A1_DURABLE_CONSUMER,
    nats_msg_id: str | None = None,
    jetstream_seq: int | None = 11,
) -> Delivery:
    body = json.dumps(envelope or valid_a1_envelope()).encode("utf-8")
    return Delivery(
        body=body,
        subject=subject,
        stream=stream,
        consumer_name=consumer_name,
        nats_msg_id=nats_msg_id,
        jetstream_seq=jetstream_seq,
    )


@pytest.fixture
def store() -> MemoryTrackingStore:
    return MemoryTrackingStore()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 30, 11, 15, 43, tzinfo=UTC)


@pytest.fixture
def coordinator(store: MemoryTrackingStore, now: datetime) -> TimelineConsumerCoordinator:
    return TimelineConsumerCoordinator(
        unit_of_work=store,
        inbox=store,
        observations=store,
        transport=RecordingTransport(store),
        consumer_name=A1_DURABLE_CONSUMER,
        handler_version="0.1.0",
        processing_owner="test-worker",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=lambda: now,
    )


@pytest.fixture
def make_delivery() -> Callable[..., Delivery]:
    return a1_delivery


@pytest.fixture
def make_envelope() -> Callable[..., dict[str, Any]]:
    return valid_a1_envelope


@pytest.fixture
def make_payload() -> Callable[..., dict[str, Any]]:
    return valid_a1_payload
