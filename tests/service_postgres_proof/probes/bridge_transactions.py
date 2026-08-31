"""Bridge repository transaction probe executed inside the service virtualenv."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from legacy_event_bridge.domain.types import CdcChange
from legacy_event_bridge.infrastructure.persistence.postgres_store import SqlAlchemyBridgeStore
from legacy_event_bridge.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    engine = build_engine(database_url)
    store = SqlAlchemyBridgeStore(build_session_factory(engine))
    now = datetime.now(UTC)
    source_pk = uuid4()
    change = CdcChange(
        source_system="legacy",
        source_table="audit_logs",
        source_pk=source_pk,
        source_position="0/100",
        capture_slot="cdc:audit_logs",
        normalized_fields={"action": "create"},
        received_at=now,
    )

    tx = store.begin()
    landing, inserted = store.insert_landing(tx, change=change, mapper_version="v1")
    assert inserted and landing is not None
    store.update_durable_landed(
        tx,
        capture_source="cdc:audit_logs",
        position="0/100",
        at=now,
    )
    tx.commit()

    checkpoint = store.get(capture_source="cdc:audit_logs")
    assert checkpoint is not None
    assert checkpoint.last_durably_landed_position == "0/100"

    tx = store.begin()
    duplicate, dup_inserted = store.insert_landing(tx, change=change, mapper_version="v1")
    tx.commit()
    assert duplicate is None and dup_inserted is False

    event_id = uuid4()
    tx = store.begin()
    store.insert(
        tx,
        event_id=event_id,
        subject="legacy_bridge.observation.audit_entry",
        payload_json={"event_id": str(event_id)},
        landing_id=landing.id,
        max_attempts=5,
        at=now,
    )
    tx.commit()

    lease_until = now + timedelta(minutes=1)
    claimed = store.claim_batch(
        owner="relay-worker-1",
        batch_size=5,
        lease_until=lease_until,
        now=now,
    )
    assert len(claimed) == 1
    assert claimed[0].event_id == event_id
    assert claimed[0].status == "processing"
    assert claimed[0].processing_owner == "relay-worker-1"

    payload = {
        "landing_checkpoint": checkpoint.last_durably_landed_position,
        "dedupe_inserted": inserted,
        "duplicate_blocked": not dup_inserted,
        "outbox_claimed": len(claimed),
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
