"""Audit repository transaction probe executed inside the service virtualenv."""
# ruff: noqa: I001

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from audit.domain.types import LegacyAuditObservation
from audit.infrastructure.persistence.session import build_engine, build_session_factory
from audit.infrastructure.persistence.sqlalchemy_store import SqlAlchemyAuditStore
from sqlalchemy.exc import IntegrityError


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    engine = build_engine(database_url)
    store = SqlAlchemyAuditStore(build_session_factory(engine))
    now = datetime.now(UTC)
    event_id = uuid4()
    consumer = "audit-a2-consumer"

    store.begin()
    inbox = store.try_insert_received(
        consumer_name=consumer,
        event_id=event_id,
        event_type="legacy_bridge.observation.audit_entry",
        event_version=1,
        handler_version="v1",
        processing_owner="worker-1",
        processing_lease_until=now + timedelta(minutes=1),
        received_at=now,
        correlation_id=uuid4(),
        jetstream_stream="AUDIT",
        jetstream_seq=42,
        nats_msg_id="msg-1",
    )
    assert inbox is not None
    observation = LegacyAuditObservation(
        event_id=event_id,
        source_system="legacy",
        source_table="audit_logs",
        source_pk=uuid4(),
        source_position="0/200",
        source_module="audit",
        audit_entry_id=uuid4(),
        action="create",
        entity_type="shipment",
        entity_id=uuid4(),
        actor_type="user",
        actor_id=uuid4(),
        source="api",
        occurred_at=now,
        bridge_mapper_version="v1",
        safe_metadata={"field": "value"},
        received_at=now,
    )
    inserted = store.insert_if_absent(observation)
    assert inserted is True
    duplicate_projection = store.insert_if_absent(observation)
    assert duplicate_projection is False
    store.mark_processed(consumer_name=consumer, event_id=event_id, processed_at=now)
    store.commit()

    assert store.get_by_event_id(event_id) is not None
    assert store.observation_count() == 1

    duplicate_blocked = False
    store.begin()
    store.try_insert_received(
        consumer_name=consumer,
        event_id=event_id,
        event_type="legacy_bridge.observation.audit_entry",
        event_version=1,
        handler_version="v1",
        processing_owner="worker-1",
        processing_lease_until=now + timedelta(minutes=1),
        received_at=now,
        correlation_id=uuid4(),
        jetstream_stream="AUDIT",
        jetstream_seq=43,
        nats_msg_id="msg-2",
    )
    try:
        store.commit()
    except IntegrityError:
        duplicate_blocked = True
        store.rollback()

    rollback_event = uuid4()
    store.begin()
    store.try_insert_received(
        consumer_name=consumer,
        event_id=rollback_event,
        event_type="legacy_bridge.observation.audit_entry",
        event_version=1,
        handler_version="v1",
        processing_owner="worker-1",
        processing_lease_until=now + timedelta(minutes=1),
        received_at=now,
        correlation_id=None,
        jetstream_stream=None,
        jetstream_seq=None,
        nats_msg_id=None,
    )
    rollback_observation = LegacyAuditObservation(
        event_id=rollback_event,
        source_system="legacy",
        source_table="audit_logs",
        source_pk=uuid4(),
        source_position="0/201",
        source_module="audit",
        audit_entry_id=uuid4(),
        action="update",
        entity_type="shipment",
        entity_id=uuid4(),
        actor_type="user",
        actor_id=None,
        source="api",
        occurred_at=now,
        bridge_mapper_version="v1",
        safe_metadata={},
        received_at=now,
    )
    store.insert_if_absent(rollback_observation)
    store.rollback()
    assert store.get_by_event_id(rollback_event) is None
    assert store.observation_count() == 1

    payload = {
        "inbox_committed": True,
        "duplicate_blocked": duplicate_blocked,
        "rollback_without_projection": store.get_by_event_id(rollback_event) is None,
        "observation_count": store.observation_count(),
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
