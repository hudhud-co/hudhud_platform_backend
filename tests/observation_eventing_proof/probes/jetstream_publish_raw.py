"""Publish raw bytes to JetStream for malformed-delivery proof scenarios."""

from __future__ import annotations

import asyncio
import base64
import json
import os

import nats


async def _publish() -> dict[str, object]:
    nats_url = os.environ.get("NATS_URL")
    subject = os.environ.get("PUBLISH_SUBJECT")
    payload_b64 = os.environ.get("PUBLISH_PAYLOAD_B64")
    if not nats_url or not subject or not payload_b64:
        msg = "NATS_URL, PUBLISH_SUBJECT, and PUBLISH_PAYLOAD_B64 are required"
        raise RuntimeError(msg)

    payload = base64.b64decode(payload_b64)
    headers = None
    msg_id = os.environ.get("PUBLISH_MSG_ID")
    if msg_id:
        headers = {"Nats-Msg-Id": msg_id}

    nc = await nats.connect(nats_url, connect_timeout=5)
    try:
        js = nc.jetstream()
        ack = await js.publish(subject, payload, headers=headers, timeout=5.0)
        return {
            "stream": ack.stream,
            "seq": ack.seq,
            "duplicate": bool(ack.duplicate),
        }
    finally:
        await nc.drain()


def main() -> int:
    result = asyncio.run(_publish())
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
