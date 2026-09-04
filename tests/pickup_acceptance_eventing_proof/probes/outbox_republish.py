"""Republish a stored outbox envelope with a new Nats-Msg-Id."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from uuid import UUID

from pickup.config import load_settings
from pickup.infrastructure.nats.client import build_live_nats_client
from pickup.infrastructure.nats.serialization import envelope_dict_to_wire_bytes
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyOutboxRelayStore


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    nats_url = os.environ.get("NATS_URL")
    event_id_raw = os.environ.get("EVENT_ID")
    msg_id = os.environ.get("PUBLISH_MSG_ID")
    if not all((database_url, nats_url, event_id_raw, msg_id)):
        print("required probe inputs missing", file=sys.stderr)
        return 1

    settings = load_settings(
        relay_enabled=True,
        database_url=database_url,
        nats_url=nats_url,
        nats_dev_no_auth=True,
    )
    engine = build_engine(database_url)
    client = None
    try:
        store = SqlAlchemyOutboxRelayStore(session_factory=build_session_factory(engine))
        row = store.get_by_event_id(UUID(event_id_raw))
        if row is None:
            print("outbox row missing", file=sys.stderr)
            return 1
        payload = envelope_dict_to_wire_bytes(row.payload_json)
        client = build_live_nats_client(settings)
        client.connect()
        ack = client.publish(
            subject=row.subject,
            payload=payload,
            msg_id=msg_id,
            timeout=settings.relay_publish_timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "puback_received": True,
                    "puback_stream": ack.stream,
                    "puback_duplicate": bool(ack.duplicate),
                }
            )
        )
        return 0
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.drain()
            with contextlib.suppress(Exception):
                client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
