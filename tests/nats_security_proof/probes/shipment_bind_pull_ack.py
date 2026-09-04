"""Shipment bind-only pull + ACK probe with TLS + creds."""

from __future__ import annotations

import asyncio
import json
import os

import nats
from shipment.config import RuntimeEnvironment, load_settings
from shipment.domain.contract import PICKUP_ACCEPTED_DURABLE_CONSUMER, PICKUP_ACCEPTED_STREAM
from shipment.infrastructure.jetstream.connection import (
    bind_existing_pull_consumer,
    build_nats_connect_options,
    verify_nats_readiness,
)
from shipment.infrastructure.jetstream.delivery import delivery_from_message


async def _run() -> dict[str, object]:
    nats_url = os.environ.get("NATS_URL")
    ca_file = os.environ.get("NATS_TLS_CA_FILE")
    creds_file = os.environ.get("NATS_CREDS_FILE")
    fetch_timeout = float(os.environ.get("FETCH_TIMEOUT_SECONDS", "3"))
    if not nats_url or not ca_file or not creds_file:
        msg = "NATS_URL, NATS_TLS_CA_FILE, and NATS_CREDS_FILE are required"
        raise RuntimeError(msg)

    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        nats_enabled=True,
        nats_url=nats_url,
        nats_tls_enabled=True,
        nats_tls_ca_file=ca_file,
        nats_creds_file=creds_file,
        allow_no_auth_local=False,
        adr_0010_credentials_configured=True,
        pull_fetch_timeout_seconds=fetch_timeout,
    )
    options = build_nats_connect_options(settings)
    nc = await nats.connect(**options)
    try:
        js = nc.jetstream()
        report = await verify_nats_readiness(js, settings=settings)
        subscription, _info = await bind_existing_pull_consumer(js, settings=settings)
        messages = await asyncio.wait_for(
            subscription.fetch(1, timeout=fetch_timeout),
            timeout=fetch_timeout + 1,
        )
        acked = 0
        subject = None
        stream = PICKUP_ACCEPTED_STREAM
        durable = PICKUP_ACCEPTED_DURABLE_CONSUMER
        if messages:
            delivery = delivery_from_message(messages[0])
            await messages[0].ack()
            acked = 1
            subject = delivery.subject
            stream = delivery.stream
            durable = delivery.consumer_name
        return {
            "binding_verified": report.binding_verified,
            "fetched": len(messages),
            "acked": acked,
            "subject": subject,
            "stream": stream,
            "durable": durable,
        }
    finally:
        await nc.close()


def main() -> int:
    try:
        result = asyncio.run(_run())
        print(json.dumps(result))
        return 0 if result.get("binding_verified") else 1
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "binding_verified": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
