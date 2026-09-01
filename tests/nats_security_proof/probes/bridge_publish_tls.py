"""Bridge TLS + creds publish probe using LiveNatsJetStreamClient."""

from __future__ import annotations

import json
import os
import sys

from legacy_event_bridge.config import BridgeSettings, RuntimeEnvironment
from legacy_event_bridge.infrastructure.nats.client import LiveNatsJetStreamClient
from legacy_event_bridge.infrastructure.nats.subjects import A2_SUBJECT


def main() -> int:
    nats_url = os.environ.get("NATS_URL")
    ca_file = os.environ.get("NATS_TLS_CA_FILE")
    creds_file = os.environ.get("NATS_CREDS_FILE")
    subject = os.environ.get("PUBLISH_SUBJECT", A2_SUBJECT)
    payload_raw = os.environ.get("PUBLISH_PAYLOAD", '{"proof":"bridge"}')
    msg_id = os.environ.get("PUBLISH_MSG_ID", "bridge-proof-msg")
    if not nats_url or not ca_file or not creds_file:
        print("NATS_URL, NATS_TLS_CA_FILE, and NATS_CREDS_FILE are required", file=sys.stderr)
        return 1

    settings = BridgeSettings(
        environment=RuntimeEnvironment.TEST,
        relay_enabled=True,
        nats_url=nats_url,
        nats_tls_enabled=True,
        nats_tls_ca_file=ca_file,
        nats_creds_file=creds_file,
        adr_0004_credentials_configured=True,
        relay_publish_timeout_seconds=5.0,
    )
    client = LiveNatsJetStreamClient(settings)
    client.connect()
    try:
        ack = client.publish(
            subject=subject,
            payload=payload_raw.encode("utf-8")
            if isinstance(payload_raw, str)
            else payload_raw,
            msg_id=msg_id,
            timeout=5.0,
        )
        print(
            json.dumps(
                {
                    "stream": ack.stream,
                    "seq": ack.seq,
                    "duplicate": ack.duplicate,
                    "puback_received": True,
                }
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"puback_received": False, "error_type": type(exc).__name__}))
        return 1
    finally:
        client.drain()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
