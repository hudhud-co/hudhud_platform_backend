"""Bridge outbox insert + real JetStream publish probe."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID

from legacy_event_bridge.application.publisher import OutboxPublisher
from legacy_event_bridge.config import BridgeSettings, RuntimeEnvironment
from legacy_event_bridge.domain.types import CdcChange
from legacy_event_bridge.infrastructure.nats.client import LiveNatsJetStreamClient
from legacy_event_bridge.infrastructure.nats.publisher import JetStreamPublisherAdapter
from legacy_event_bridge.infrastructure.nats.subjects import A2_SUBJECT
from legacy_event_bridge.infrastructure.persistence.models import BridgeOutboxRow
from legacy_event_bridge.infrastructure.persistence.postgres_store import SqlAlchemyBridgeStore
from legacy_event_bridge.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from sqlalchemy import select


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    nats_url = os.environ.get("NATS_URL")
    envelope_raw = os.environ.get("ENVELOPE_JSON")
    if not database_url or not nats_url or not envelope_raw:
        print("DATABASE_URL, NATS_URL, and ENVELOPE_JSON are required", file=sys.stderr)
        return 1

    envelope = json.loads(envelope_raw)
    event_id = UUID(str(envelope["event_id"]))

    engine = build_engine(database_url)
    store = SqlAlchemyBridgeStore(build_session_factory(engine))
    now = datetime.now(UTC)
    source_pk = UUID(str(envelope["payload"]["source_pk"]))
    change = CdcChange(
        source_system="legacy",
        source_table="audit_logs",
        source_pk=source_pk,
        source_position=str(envelope["payload"]["source_position"]),
        capture_slot="proof:audit_logs",
        normalized_fields={"action": envelope["payload"].get("action", "proof")},
        received_at=now,
    )

    tx = store.begin()
    landing, _inserted = store.insert_landing(tx, change=change, mapper_version="1.0.0")
    assert landing is not None
    store.insert(
        tx,
        event_id=event_id,
        subject=A2_SUBJECT,
        payload_json=envelope,
        landing_id=landing.id,
        max_attempts=5,
        at=now,
    )
    tx.commit()

    settings = BridgeSettings(
        environment=RuntimeEnvironment.TEST,
        database_url=database_url,
        relay_enabled=True,
        nats_url=nats_url,
        nats_dev_no_auth=True,
        relay_owner_id="obs-proof-relay",
        relay_batch_size=2,
        relay_publish_timeout_seconds=5.0,
    )
    nats_client = LiveNatsJetStreamClient(settings)
    nats_client.connect()
    try:
        adapter = JetStreamPublisherAdapter(nats_client, publish_timeout_seconds=5.0)
        publisher = OutboxPublisher(
            outbox_store=store,
            publisher=adapter,
            owner_id="obs-proof-relay",
            batch_size=2,
            lease_seconds=30,
        )
        outcome = publisher.publish_pending()
    finally:
        nats_client.drain()
        nats_client.close()

    outbox_status = _outbox_status(store, event_id)
    payload = {
        "event_id": str(event_id),
        "published_count": outcome.published_count,
        "outbox_status": outbox_status,
        "puback_received": outcome.published_count == 1,
    }
    print(json.dumps(payload))
    return 0 if payload["puback_received"] else 1


def _outbox_status(store: SqlAlchemyBridgeStore, event_id: UUID) -> str:
    with store.session_factory() as session:
        row = session.execute(
            select(BridgeOutboxRow).where(BridgeOutboxRow.event_id == event_id)
        ).scalar_one()
        return str(row.status)


if __name__ == "__main__":
    raise SystemExit(main())
