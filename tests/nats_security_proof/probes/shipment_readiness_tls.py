"""Shipment readiness-only probe for rotation/revocation checks."""

from __future__ import annotations

import asyncio
import json
import os

import nats
from shipment.config import RuntimeEnvironment, load_settings
from shipment.infrastructure.jetstream.connection import (
    build_nats_connect_options,
    verify_nats_readiness,
)


async def _run() -> dict[str, object]:
    nats_url = os.environ.get("NATS_URL")
    ca_file = os.environ.get("NATS_TLS_CA_FILE")
    creds_file = os.environ.get("NATS_CREDS_FILE")
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
    )
    options = build_nats_connect_options(settings)
    nc = await nats.connect(**options)
    try:
        js = nc.jetstream()
        report = await verify_nats_readiness(js, settings=settings)
        return {
            "binding_verified": report.binding_verified,
            "stream": report.stream,
            "durable_name": report.durable_name,
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
