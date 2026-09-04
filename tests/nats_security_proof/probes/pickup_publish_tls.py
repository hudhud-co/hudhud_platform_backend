"""Pickup TLS + creds publish probe using LiveNatsJetStreamClient."""

from __future__ import annotations

import json
import os
import sys
from uuid import uuid4

from pickup.config import PickupSettings, RuntimeEnvironment
from pickup.infrastructure.contracts.registry import validate_accepted_fact_envelope
from pickup.infrastructure.nats.client import LiveNatsJetStreamClient
from pickup.infrastructure.nats.publisher import JetStreamPublisherAdapter
from pickup.infrastructure.nats.subjects import ACCEPTED_SUBJECT, STREAM_PICKUP


def _accepted_envelope() -> dict[str, object]:
    pickup_task_id = str(uuid4())
    event_id = str(uuid4())
    return {
        "envelope_version": 1,
        "event_id": event_id,
        "event_type": "pickup.fact.accepted",
        "event_version": 1,
        "occurred_at": "2026-09-04T12:00:00.000Z",
        "producer": "pickup",
        "message_kind": "integration",
        "aggregate_scope": "aggregate",
        "aggregate_type": "pickup_task",
        "aggregate_id": pickup_task_id,
        "aggregate_version": 3,
        "correlation_id": str(uuid4()),
        "data_classification": "internal",
        "pii_present": False,
        "payload": {
            "pickup_task_id": pickup_task_id,
            "shipment_id": str(uuid4()),
            "outcome": "ACCEPTED",
            "accepted_at": "2026-09-04T12:00:00.000Z",
            "assigned_driver_user_id": "driver-42",
            "acting_driver_user_id": "driver-42",
            "scanned_identifier": "WB-1001",
        },
    }


def main() -> int:
    nats_url = os.environ.get("NATS_URL")
    ca_file = os.environ.get("NATS_TLS_CA_FILE")
    creds_file = os.environ.get("NATS_CREDS_FILE")
    envelope_raw = os.environ.get("PUBLISH_ENVELOPE")
    if not nats_url or not ca_file or not creds_file:
        print("NATS_URL, NATS_TLS_CA_FILE, and NATS_CREDS_FILE are required", file=sys.stderr)
        return 1

    envelope = json.loads(envelope_raw) if envelope_raw else _accepted_envelope()
    msg_id = os.environ.get("PUBLISH_MSG_ID", str(envelope["event_id"]))
    validate_accepted_fact_envelope(envelope)

    settings = PickupSettings(
        environment=RuntimeEnvironment.TEST,
        relay_enabled=True,
        nats_url=nats_url,
        nats_tls_enabled=True,
        nats_tls_ca_file=ca_file,
        nats_creds_file=creds_file,
        adr_0010_credentials_configured=True,
        relay_publish_timeout_seconds=5.0,
    )
    client = LiveNatsJetStreamClient(settings)
    adapter = JetStreamPublisherAdapter(client, publish_timeout_seconds=5.0)
    client.connect()
    try:
        result = adapter.publish(
            subject=ACCEPTED_SUBJECT,
            payload_json=envelope,
            transport_msg_id=msg_id,
        )
        print(
            json.dumps(
                {
                    "stream": STREAM_PICKUP,
                    "subject": ACCEPTED_SUBJECT,
                    "puback_received": result.ack_received,
                    "error_code": result.error_code,
                    "adapter": "JetStreamPublisherAdapter",
                }
            )
        )
        return 0 if result.ack_received else 1
    except Exception as exc:
        print(json.dumps({"puback_received": False, "error_type": type(exc).__name__}))
        return 1
    finally:
        adapter.drain()
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
