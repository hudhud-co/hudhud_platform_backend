"""TLS + creds NATS connection helpers for security proof probes."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any

import nats

from .constants import CONNECT_TIMEOUT_SECONDS


def build_tls_context(ca_file: Path, *, server_hostname: str | None = None) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(ca_file))
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = server_hostname is not None
    return context


async def connect_nats(
    *,
    nats_url: str,
    creds_file: Path,
    ca_file: Path,
    server_hostname: str | None = None,
    allow_plaintext: bool = False,
) -> Any:
    connect_kwargs: dict[str, Any] = {
        "servers": [nats_url],
        "user_credentials": str(creds_file),
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    }
    if not allow_plaintext:
        connect_kwargs["tls"] = build_tls_context(ca_file, server_hostname=server_hostname)
    return await asyncio.wait_for(
        nats.connect(**connect_kwargs),
        timeout=CONNECT_TIMEOUT_SECONDS,
    )


async def publish_js(
    *,
    nats_url: str,
    creds_file: Path,
    ca_file: Path,
    subject: str,
    payload: bytes,
    msg_id: str | None = None,
) -> dict[str, Any]:
    nc = await connect_nats(nats_url=nats_url, creds_file=creds_file, ca_file=ca_file)
    try:
        js = nc.jetstream()
        headers = {"Nats-Msg-Id": msg_id} if msg_id else None
        ack = await asyncio.wait_for(
            js.publish(subject, payload, headers=headers),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        return {"stream": ack.stream, "seq": ack.seq, "duplicate": ack.duplicate}
    finally:
        await nc.drain()


async def consumer_info(
    *,
    nats_url: str,
    creds_file: Path,
    ca_file: Path,
    stream: str,
    durable: str,
) -> dict[str, Any]:
    nc = await connect_nats(nats_url=nats_url, creds_file=creds_file, ca_file=ca_file)
    try:
        js = nc.jetstream()
        info = await asyncio.wait_for(
            js.consumer_info(stream, durable),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        return {
            "stream": info.stream_name,
            "durable": info.config.durable_name,
            "filter": info.config.filter_subject,
        }
    finally:
        await nc.drain()


async def pull_and_ack_one(
    *,
    nats_url: str,
    creds_file: Path,
    ca_file: Path,
    stream: str,
    durable: str,
    fetch_timeout: float,
) -> dict[str, Any]:
    nc = await connect_nats(nats_url=nats_url, creds_file=creds_file, ca_file=ca_file)
    try:
        js = nc.jetstream()
        sub = await js.pull_subscribe_bind(durable=durable, stream=stream)
        messages = await asyncio.wait_for(
            sub.fetch(1, timeout=fetch_timeout),
            timeout=fetch_timeout + 1,
        )
        if not messages:
            return {"fetched": 0, "acked": 0}
        await messages[0].ack()
        return {"fetched": 1, "acked": 1, "subject": messages[0].subject}
    finally:
        await nc.drain()
