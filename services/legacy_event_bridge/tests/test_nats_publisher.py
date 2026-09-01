"""JetStream publisher adapter tests — fake broker only."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from event_envelope.enums import AggregateScope, DataClassification, MessageKind
from event_envelope.envelope import EventEnvelope
from event_envelope.serde import envelope_to_json_dict
from fakes.nats_fake import FakeJetStreamClient

from legacy_event_bridge.application.publisher import OutboxPublisher
from legacy_event_bridge.config import RuntimeEnvironment, load_settings
from legacy_event_bridge.infrastructure.memory import MemoryBridgeStore
from legacy_event_bridge.infrastructure.nats.client import assert_nats_configuration
from legacy_event_bridge.infrastructure.nats.errors import NatsNotConfiguredError
from legacy_event_bridge.infrastructure.nats.publisher import JetStreamPublisherAdapter
from legacy_event_bridge.infrastructure.nats.serialization import envelope_dict_to_wire_bytes
from legacy_event_bridge.infrastructure.nats.subjects import (
    A1_SUBJECT,
    A2_SUBJECT,
    STREAM_AUDIT,
)


def _sample_envelope_dict() -> dict:
    event_id = uuid4()
    envelope = EventEnvelope(
        event_id=event_id,
        event_type="legacy_bridge.observation.shipment_timeline_entry",
        event_version=1,
        occurred_at=datetime(2026, 8, 30, 11, 15, 42, 456000, tzinfo=UTC),
        producer="legacy_bridge",
        message_kind=MessageKind.INTEGRATION,
        aggregate_scope=AggregateScope.NON_AGGREGATE,
        correlation_id=event_id,
        data_classification=DataClassification.INTERNAL,
        pii_present=False,
        schema_uri="https://hudhud.platform/contracts/events/test/v1.schema.json",
        payload={"source_table": "shipment_events", "occurred_at": "2026-08-30T11:15:42.456Z"},
        metadata={"replay": False},
    )
    return envelope_to_json_dict(envelope)


def test_subject_and_stream_mapping_a1() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    payload = _sample_envelope_dict()
    result = adapter.publish(
        subject=A1_SUBJECT,
        payload_json=payload,
        transport_msg_id=str(payload["event_id"]),
    )
    assert result.ack_received
    subject, _body, _msg_id = fake.publish_log[0]
    assert subject == A1_SUBJECT
    assert fake.publish_log[0][0] == A1_SUBJECT


def test_subject_and_stream_mapping_a2() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    payload = _sample_envelope_dict()
    payload["event_type"] = "legacy_bridge.observation.audit_entry"
    result = adapter.publish(
        subject=A2_SUBJECT,
        payload_json=payload,
        transport_msg_id=str(payload["event_id"]),
    )
    assert result.ack_received
    assert fake.publish_log[0][0] == A2_SUBJECT


def test_exact_published_bytes_and_msg_id() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    payload = _sample_envelope_dict()
    event_id = str(payload["event_id"])
    adapter.publish(
        subject=A1_SUBJECT,
        payload_json=payload,
        transport_msg_id=event_id,
    )
    _, body, msg_id = fake.publish_log[0]
    assert msg_id == event_id
    assert body == envelope_dict_to_wire_bytes(payload)


def test_unexpected_stream_ack_is_permanent() -> None:
    fake = FakeJetStreamClient(default_stream=STREAM_AUDIT)
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    result = adapter.publish(
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "SUBJECT_FORBIDDEN"


def test_timeout_is_transient() -> None:
    fake = FakeJetStreamClient(should_timeout=True)
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=0.01)
    result = adapter.publish(
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "NATS_TIMEOUT"


def test_no_topology_mutation_calls() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    adapter.publish(
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert fake.topology_calls == []


def test_secret_safe_publish_errors() -> None:
    fake = FakeJetStreamClient()
    fake.should_fail_transient = True
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    result = adapter.publish(
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="token=super-secret",
    )
    assert "super-secret" not in (result.error_message or "")


def test_oversized_envelope_rejected_before_publish() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(
        fake,
        publish_timeout_seconds=1.0,
        transport_max_msg_bytes=32,
    )
    result = adapter.publish(
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "PAYLOAD_TOO_LARGE"
    assert fake.publish_log == []


def test_nats_configuration_gates() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_dev_no_auth=False,
    )
    with pytest.raises(NatsNotConfiguredError, match="credentials"):
        assert_nats_configuration(settings)

    dev_settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_dev_no_auth=True,
    )
    assert_nats_configuration(dev_settings)


def test_graceful_drain_and_close() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    adapter.drain()
    adapter.close()
    assert fake.drained
    assert fake.closed


def test_lost_ack_redelivery_same_msg_id(store: MemoryBridgeStore) -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    row = store.insert(
        store.begin(),
        event_id=uuid4(),
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        landing_id=uuid4(),
        max_attempts=5,
        at=datetime.now(tz=UTC),
    )
    store.begin().commit()
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=adapter,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
    )
    publisher.publish_pending()
    first_msg_id = fake.publish_log[0][2]
    stale_row = store.outbox[row.id]
    store.outbox[row.id] = replace(
        stale_row,
        status="pending",
        next_attempt_at=datetime.now(tz=UTC),
        processing_owner=None,
        processing_until=None,
    )
    publisher.publish_pending()
    assert fake.publish_log[1][2] == first_msg_id
