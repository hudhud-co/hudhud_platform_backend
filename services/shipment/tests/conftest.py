"""Shared fixtures for pickup.fact.accepted inbox consumer tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from shipment.application.accepted_fact_apply import NativePickupAcceptedApplyService
from shipment.application.accepted_fact_coordinator import PickupAcceptedFactCoordinator
from shipment.domain.contract import (
    PICKUP_ACCEPTED_DURABLE_CONSUMER,
    PICKUP_ACCEPTED_EVENT_TYPE,
    PICKUP_ACCEPTED_STREAM,
    PICKUP_ACCEPTED_SUBJECT,
)
from shipment.domain.entities import Shipment
from shipment.domain.types import Delivery
from shipment.domain.value_objects import ShipmentStatus, WaybillIdentity
from shipment.infrastructure.accepted_fact_memory import (
    MemoryAcceptedFactStore,
    RecordingTransport,
)

PICKUP_TASK_ID = UUID("f2faafa9-bca5-491f-b4b6-ef3b9884206c")
SHIPMENT_ID = UUID("835aa643-3538-4cb1-ae26-9beb65224618")
EVENT_ID = UUID("a1eb92fb-3f7d-4a8c-be2e-bcc8979ae574")
CORRELATION_ID = UUID("3a7e43e5-0014-4a89-abe5-5e927cf3a3e9")
ORDER_ID = UUID("11111111-2222-4333-8444-555555555555")
ACCEPTED_AT = "2026-09-04T12:00:00.000Z"
DRIVER_ID = "driver-42"
WAYBILL = "WB-1001"


def valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pickup_task_id": str(PICKUP_TASK_ID),
        "shipment_id": str(SHIPMENT_ID),
        "outcome": "ACCEPTED",
        "accepted_at": ACCEPTED_AT,
        "assigned_driver_user_id": DRIVER_ID,
        "acting_driver_user_id": DRIVER_ID,
        "scanned_identifier": WAYBILL,
    }
    payload.update(overrides)
    return payload


def valid_envelope(**overrides: Any) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "envelope_version": 1,
        "event_id": str(EVENT_ID),
        "event_type": PICKUP_ACCEPTED_EVENT_TYPE,
        "event_version": 1,
        "occurred_at": ACCEPTED_AT,
        "producer": "pickup",
        "message_kind": "integration",
        "aggregate_scope": "aggregate",
        "aggregate_type": "pickup_task",
        "aggregate_id": str(PICKUP_TASK_ID),
        "aggregate_version": 3,
        "correlation_id": str(CORRELATION_ID),
        "data_classification": "internal",
        "pii_present": False,
        "schema_uri": "https://hudhud.platform/contracts/events/pickup.fact.accepted/v1.schema.json",
        "payload": valid_payload(),
    }
    if "payload" in overrides:
        envelope["payload"] = overrides.pop("payload")
    envelope.update(overrides)
    return envelope


def make_delivery_from_envelope(
    envelope: dict[str, Any] | None = None,
    *,
    subject: str = PICKUP_ACCEPTED_SUBJECT,
    stream: str = PICKUP_ACCEPTED_STREAM,
    consumer_name: str = PICKUP_ACCEPTED_DURABLE_CONSUMER,
    nats_msg_id: str | None = None,
    jetstream_seq: int | None = 7,
) -> Delivery:
    body = json.dumps(envelope or valid_envelope()).encode("utf-8")
    return Delivery(
        body=body,
        subject=subject,
        stream=stream,
        consumer_name=consumer_name,
        nats_msg_id=nats_msg_id,
        jetstream_seq=jetstream_seq,
    )


def seed_created_shipment(
    store: MemoryAcceptedFactStore,
    *,
    shipment_id: UUID = SHIPMENT_ID,
    waybill_number: str = WAYBILL,
    version: int = 1,
) -> Shipment:
    shipment = Shipment(
        shipment_id=shipment_id,
        order_id=ORDER_ID,
        waybill_identity=WaybillIdentity(
            waybill_number=waybill_number,
            shipment_id=str(shipment_id),
        ),
        current_status=ShipmentStatus.CREATED,
        order_created_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
        version=version,
    )
    store.seed_shipment(shipment)
    return shipment


@pytest.fixture
def store() -> MemoryAcceptedFactStore:
    store = MemoryAcceptedFactStore()
    seed_created_shipment(store)
    return store


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 4, 12, 0, 5, tzinfo=UTC)


@pytest.fixture
def coordinator(store: MemoryAcceptedFactStore, now: datetime) -> PickupAcceptedFactCoordinator:
    apply_service = NativePickupAcceptedApplyService(store)
    return PickupAcceptedFactCoordinator(
        unit_of_work=store,
        inbox=store,
        transport=RecordingTransport(store),
        apply_service=apply_service,
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        handler_version="test-handler",
        processing_owner="test-owner",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=lambda: now,
    )


@pytest.fixture
def make_delivery() -> Callable[..., Delivery]:
    def _make(
        envelope: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Delivery:
        return make_delivery_from_envelope(envelope, **kwargs)

    return _make


@pytest.fixture
def make_envelope() -> Callable[..., dict[str, Any]]:
    return valid_envelope


@pytest.fixture
def make_payload() -> Callable[..., dict[str, Any]]:
    return valid_payload
