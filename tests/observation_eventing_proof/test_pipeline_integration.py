"""Live Bridge→Audit A2 observation path integration proof."""

from __future__ import annotations

import json
from hashlib import sha256
from uuid import NAMESPACE_OID, UUID, uuid5

import pytest

from .constants import (
    A2_DURABLE,
    A2_STREAM,
    A2_SUBJECT,
    AUDIT_DATABASE,
    AUDIT_EXPECTED_HEAD,
    BRIDGE_DATABASE,
    BRIDGE_EXPECTED_HEAD,
    HANDLER_CONCURRENCY,
)
from .envelope import build_a2_envelope
from .helpers import (
    AUDIT_SERVICE,
    BRIDGE_SERVICE,
    alembic_current_revision,
    audit_owner_url,
    bridge_owner_url,
    inbox_error_code_for_event,
    inbox_status_for_event,
    observation_exists,
    outbox_status_for_event,
    run_audit_poll_once,
    run_bridge_outbox_publish,
    run_jetstream_publish_raw,
    table_count,
)

pytestmark = pytest.mark.integration


def test_service_databases_isolated_and_at_migration_head(eventing_proof_stack: dict) -> None:
    port = eventing_proof_stack["postgres_port"]
    assert alembic_current_revision(BRIDGE_SERVICE, bridge_owner_url(port)) == BRIDGE_EXPECTED_HEAD
    assert alembic_current_revision(AUDIT_SERVICE, audit_owner_url(port)) == AUDIT_EXPECTED_HEAD


def test_valid_a2_end_to_end(eventing_proof_stack: dict) -> None:
    envelope = build_a2_envelope()
    event_id = envelope["event_id"]
    bridge_url = eventing_proof_stack["bridge_database_url"]
    audit_url = eventing_proof_stack["audit_database_url"]
    nats_url = eventing_proof_stack["nats_url"]

    publish = run_bridge_outbox_publish(
        database_url=bridge_url,
        nats_url=nats_url,
        envelope_json=json.dumps(envelope),
    )
    assert publish["puback_received"] is True
    assert publish["outbox_status"] == "published"
    assert outbox_status_for_event(BRIDGE_DATABASE, event_id) == "published"

    poll = run_audit_poll_once(database_url=audit_url, nats_url=nats_url)
    assert poll["processed"] == 1
    assert poll["handler_concurrency"] == HANDLER_CONCURRENCY

    assert inbox_status_for_event(AUDIT_DATABASE, event_id) == "processed"
    assert observation_exists(AUDIT_DATABASE, event_id)
    assert table_count(AUDIT_DATABASE, "legacy_audit_observations") >= 1


def test_duplicate_event_dedupes_projection(eventing_proof_stack: dict) -> None:
    envelope = build_a2_envelope()
    event_id = envelope["event_id"]
    bridge_url = eventing_proof_stack["bridge_database_url"]
    audit_url = eventing_proof_stack["audit_database_url"]
    nats_url = eventing_proof_stack["nats_url"]

    run_bridge_outbox_publish(
        database_url=bridge_url,
        nats_url=nats_url,
        envelope_json=json.dumps(envelope),
    )
    run_audit_poll_once(database_url=audit_url, nats_url=nats_url)
    before = table_count(AUDIT_DATABASE, "legacy_audit_observations")

    republish = run_jetstream_publish_raw(
        nats_url=nats_url,
        subject=A2_SUBJECT,
        payload=json.dumps(envelope).encode("utf-8"),
        msg_id=f"dup-{event_id}",
    )
    assert republish["stream"] == A2_STREAM

    for _ in range(3):
        poll = run_audit_poll_once(database_url=audit_url, nats_url=nats_url)
        if poll["processed"] == 0:
            break
    assert table_count(AUDIT_DATABASE, "legacy_audit_observations") == before
    assert inbox_status_for_event(AUDIT_DATABASE, event_id) == "processed"


def test_malformed_delivery_quarantines_without_projection(eventing_proof_stack: dict) -> None:
    audit_url = eventing_proof_stack["audit_database_url"]
    nats_url = eventing_proof_stack["nats_url"]
    malformed = b"{not-json"

    publish = run_jetstream_publish_raw(
        nats_url=nats_url,
        subject=A2_SUBJECT,
        payload=malformed,
        msg_id="malformed-proof-1",
    )
    assert publish["stream"] == A2_STREAM
    seq = int(publish["seq"])

    fingerprint = _poison_fingerprint(
        consumer_name=A2_DURABLE,
        subject=A2_SUBJECT,
        jetstream_seq=seq,
        body_prefix=malformed[:256],
    )
    for _ in range(3):
        run_audit_poll_once(database_url=audit_url, nats_url=nats_url)
        if inbox_status_for_event(AUDIT_DATABASE, str(fingerprint)) == "quarantined":
            break
    assert inbox_status_for_event(AUDIT_DATABASE, str(fingerprint)) == "quarantined"
    assert inbox_error_code_for_event(AUDIT_DATABASE, str(fingerprint)) == "DESERIALIZE_FAILURE"
    assert not observation_exists(AUDIT_DATABASE, str(fingerprint))


def test_commit_before_ack_recovery(eventing_proof_stack: dict) -> None:
    envelope = build_a2_envelope()
    event_id = envelope["event_id"]
    bridge_url = eventing_proof_stack["bridge_database_url"]
    audit_url = eventing_proof_stack["audit_database_url"]
    nats_url = eventing_proof_stack["nats_url"]

    run_bridge_outbox_publish(
        database_url=bridge_url,
        nats_url=nats_url,
        envelope_json=json.dumps(envelope),
    )

    first = run_audit_poll_once(
        database_url=audit_url,
        nats_url=nats_url,
        fail_first_ack=True,
    )
    assert first["processed"] >= 1
    assert inbox_status_for_event(AUDIT_DATABASE, event_id) == "processed"
    before = table_count(AUDIT_DATABASE, "legacy_audit_observations")

    for _ in range(3):
        second = run_audit_poll_once(database_url=audit_url, nats_url=nats_url)
        if second["processed"] >= 1:
            break
    assert table_count(AUDIT_DATABASE, "legacy_audit_observations") == before
    assert inbox_status_for_event(AUDIT_DATABASE, event_id) == "processed"


def _poison_fingerprint(
    *,
    consumer_name: str,
    subject: str,
    jetstream_seq: int,
    body_prefix: bytes,
) -> UUID:
    digest = sha256(
        b"|".join(
            [
                consumer_name.encode(),
                subject.encode(),
                str(jetstream_seq).encode(),
                body_prefix,
            ]
        )
    ).hexdigest()
    return uuid5(NAMESPACE_OID, f"audit-poison:{digest}")
