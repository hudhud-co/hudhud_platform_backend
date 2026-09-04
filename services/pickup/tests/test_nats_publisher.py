"""JetStream publisher adapter tests — fake broker only."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fakes.nats_fake import FakeJetStreamClient

from pickup.application.publisher import OutboxPublisher
from pickup.config import RuntimeEnvironment, load_settings
from pickup.domain.entities import OutboxRecord
from pickup.domain.value_objects import OutboxStatus
from pickup.infrastructure.memory import InMemoryPickupUnitOfWork
from pickup.infrastructure.nats.client import assert_nats_configuration
from pickup.infrastructure.nats.errors import NatsNotConfiguredError
from pickup.infrastructure.nats.publisher import JetStreamPublisherAdapter
from pickup.infrastructure.nats.serialization import envelope_dict_to_wire_bytes
from pickup.infrastructure.nats.subjects import ACCEPTED_SUBJECT, STREAM_PICKUP


def _sample_envelope_dict(*, event_id: UUID | None = None) -> dict:
    eid = event_id or UUID("a1eb92fb-3f7d-4a8c-be2e-bcc8979ae574")
    aggregate_id = UUID("f2faafa9-bca5-491f-b4b6-ef3b9884206c")
    return {
        "envelope_version": 1,
        "event_id": str(eid),
        "event_type": "pickup.fact.accepted",
        "event_version": 1,
        "occurred_at": "2026-09-04T12:00:00.000Z",
        "producer": "pickup",
        "message_kind": "integration",
        "aggregate_scope": "aggregate",
        "aggregate_type": "pickup_task",
        "aggregate_id": str(aggregate_id),
        "aggregate_version": 3,
        "correlation_id": "3a7e43e5-0014-4a89-abe5-5e927cf3a3e9",
        "data_classification": "internal",
        "pii_present": False,
        "payload": {
            "pickup_task_id": str(aggregate_id),
            "shipment_id": "835aa643-3538-4cb1-ae26-9beb65224618",
            "outcome": "ACCEPTED",
            "accepted_at": "2026-09-04T12:00:00.000Z",
            "assigned_driver_user_id": "driver-42",
            "acting_driver_user_id": "driver-42",
            "scanned_identifier": "WB-1001",
        },
    }


def _insert_pending(
    store: InMemoryPickupUnitOfWork,
    *,
    event_id: UUID | None = None,
    subject: str = ACCEPTED_SUBJECT,
    max_attempts: int = 5,
    payload: dict | None = None,
) -> OutboxRecord:
    eid = event_id or uuid4()
    now = datetime.now(tz=UTC)
    envelope = payload if payload is not None else _sample_envelope_dict(event_id=eid)
    record = OutboxRecord(
        id=uuid4(),
        event_id=eid,
        subject=subject,
        event_type="pickup.fact.accepted",
        event_version=1,
        aggregate_id=UUID(str(envelope["aggregate_id"])),
        aggregate_version=int(envelope["aggregate_version"]),
        payload_json=envelope,
        status=OutboxStatus.PENDING,
        attempt_count=0,
        max_attempts=max_attempts,
        next_attempt_at=now,
        processing_owner=None,
        processing_until=None,
        published_at=None,
        last_error_code=None,
        last_error_message=None,
        created_at=now,
    )
    store.begin()
    store.outbox.insert(record)
    store.commit()
    return record


def test_exact_subject_and_stream() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    payload = _sample_envelope_dict()
    result = adapter.publish(
        subject=ACCEPTED_SUBJECT,
        payload_json=payload,
        transport_msg_id=str(payload["event_id"]),
    )
    assert result.ack_received
    subject, _body, _msg_id = fake.publish_log[0]
    assert subject == ACCEPTED_SUBJECT
    assert subject == "hudhud.pickup.pickup.fact.accepted.v1"
    assert FakeJetStreamClient().publish(
        subject=ACCEPTED_SUBJECT,
        payload=b"{}",
        msg_id="x",
        timeout=1.0,
    ).stream == STREAM_PICKUP


def test_exact_published_bytes_and_msg_id() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    payload = _sample_envelope_dict()
    event_id = str(payload["event_id"])
    adapter.publish(
        subject=ACCEPTED_SUBJECT,
        payload_json=payload,
        transport_msg_id=event_id,
    )
    _, body, msg_id = fake.publish_log[0]
    assert msg_id == event_id
    assert body == envelope_dict_to_wire_bytes(payload)


def test_puback_required_for_success() -> None:
    fake = FakeJetStreamClient(should_timeout=True)
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=0.01)
    result = adapter.publish(
        subject=ACCEPTED_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "NATS_TIMEOUT"


def test_unexpected_stream_ack_is_permanent() -> None:
    fake = FakeJetStreamClient(default_stream="HUDHUD_SHIPMENT")
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    result = adapter.publish(
        subject=ACCEPTED_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "SUBJECT_FORBIDDEN"


def test_timeout_is_transient() -> None:
    fake = FakeJetStreamClient(should_timeout=True)
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=0.01)
    result = adapter.publish(
        subject=ACCEPTED_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "NATS_TIMEOUT"


def test_no_topology_mutation_calls() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    adapter.publish(
        subject=ACCEPTED_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert fake.topology_calls == []


def test_secret_safe_publish_errors() -> None:
    fake = FakeJetStreamClient(should_fail_transient=True)
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    result = adapter.publish(
        subject=ACCEPTED_SUBJECT,
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
        subject=ACCEPTED_SUBJECT,
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "PAYLOAD_TOO_LARGE"
    assert fake.publish_log == []


def test_invalid_envelope_rejected_before_publish() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    bad = copy.deepcopy(_sample_envelope_dict())
    bad["producer"] = "shipment"
    result = adapter.publish(
        subject=ACCEPTED_SUBJECT,
        payload_json=bad,
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "ENVELOPE_INVALID"
    assert fake.publish_log == []


def test_forbidden_subject_rejected() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    result = adapter.publish(
        subject="hudhud.wallet.forbidden.event.v1",
        payload_json=_sample_envelope_dict(),
        transport_msg_id="msg-id",
    )
    assert not result.ack_received
    assert result.error_code == "SUBJECT_FORBIDDEN"
    assert fake.publish_log == []


def test_nats_configuration_accepts_creds_file() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        relay_enabled=True,
        nats_url="tls://127.0.0.1:4222",
        nats_tls_enabled=True,
        nats_creds_file="/tmp/disposable-proof.creds",
        adr_0010_credentials_configured=True,
    )
    assert_nats_configuration(settings)
    assert settings.relay_configuration_valid()


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


def test_production_requires_adr_0010_tls_and_credentials() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_dev_no_auth=True,
        adr_0010_credentials_configured=True,
        database_url="postgresql+psycopg://localhost/pickup",
    )
    with pytest.raises(NatsNotConfiguredError, match="no-auth"):
        assert_nats_configuration(settings)

    missing_tls = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        relay_enabled=True,
        nats_url="tls://127.0.0.1:4222",
        nats_tls_enabled=False,
        nats_creds_file="/tmp/disposable.creds",
        adr_0010_credentials_configured=True,
        database_url="postgresql+psycopg://localhost/pickup",
    )
    with pytest.raises(NatsNotConfiguredError, match="TLS"):
        assert_nats_configuration(missing_tls)

    prod = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        relay_enabled=True,
        nats_url="tls://127.0.0.1:4222",
        nats_tls_enabled=True,
        nats_creds_file="/tmp/disposable.creds",
        adr_0010_credentials_configured=True,
        database_url="postgresql+psycopg://localhost/pickup",
    )
    assert_nats_configuration(prod)


def test_staging_requires_tls_credentials_and_forbids_no_auth() -> None:
    plaintext = load_settings(
        environment=RuntimeEnvironment.STAGING,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_user="pickup",
        nats_password="not-a-real-secret",
        adr_0010_credentials_configured=True,
        nats_tls_enabled=False,
    )
    with pytest.raises(NatsNotConfiguredError, match="TLS"):
        assert_nats_configuration(plaintext)

    no_auth = load_settings(
        environment=RuntimeEnvironment.STAGING,
        relay_enabled=True,
        nats_url="tls://127.0.0.1:4222",
        nats_tls_enabled=True,
        nats_dev_no_auth=True,
        adr_0010_credentials_configured=True,
    )
    with pytest.raises(NatsNotConfiguredError, match="no-auth"):
        assert_nats_configuration(no_auth)

    staging = load_settings(
        environment=RuntimeEnvironment.STAGING,
        relay_enabled=True,
        nats_url="tls://127.0.0.1:4222",
        nats_tls_enabled=True,
        nats_creds_file="/tmp/disposable.creds",
        adr_0010_credentials_configured=True,
    )
    assert_nats_configuration(staging)


def test_graceful_drain_and_close() -> None:
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    adapter.drain()
    adapter.close()
    assert fake.drained
    assert fake.closed


def test_lost_ack_redelivery_same_msg_id() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient()
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    row = _insert_pending(store)
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=adapter,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
    )
    publisher.publish_pending()
    first_msg_id = fake.publish_log[0][2]
    published = store.outbox.get_by_event_id(row.event_id)
    assert published is not None
    assert published.status is OutboxStatus.PUBLISHED

    # Simulate lost ACK / crash before DB mark — row still pending with same event_id.
    store._outbox[row.id] = OutboxRecord(
        id=row.id,
        event_id=row.event_id,
        subject=row.subject,
        event_type=row.event_type,
        event_version=row.event_version,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        payload_json=row.payload_json,
        status=OutboxStatus.PENDING,
        attempt_count=1,
        max_attempts=row.max_attempts,
        next_attempt_at=datetime.now(tz=UTC),
        processing_owner=None,
        processing_until=None,
        published_at=None,
        last_error_code=None,
        last_error_message=None,
        created_at=row.created_at,
    )
    publisher.publish_pending()
    assert fake.publish_log[1][2] == first_msg_id
    assert fake.publish_log[1][2] == str(row.event_id)
