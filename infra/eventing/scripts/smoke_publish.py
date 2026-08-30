"""Publish/consume smoke check against internal Compose NATS URL."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import nats


async def main() -> int:
    nats_url = os.environ.get("NATS_URL", "nats://nats:4222")
    subject = "hudhud.shipment.integration.smoke.v1"
    payload = f'{{"event_id":"{uuid.uuid4()}","smoke":true}}'.encode()

    nc = await nats.connect(nats_url, connect_timeout=10)
    js = nc.jetstream()
    ack = await js.publish(subject, payload)
    stream_info = await js.stream_info("HUDHUD_SHIPMENT")
    await nc.drain()

    print(f"published_seq={ack.seq}")
    print(f"stream_messages={stream_info.state.messages}")
    print("HUDHUD_EVENTING_SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
