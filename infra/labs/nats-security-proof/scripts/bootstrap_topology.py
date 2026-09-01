"""Idempotent JetStream bootstrap for NATS security proof topology only.

Creates only HUDHUD_SHIPMENT, HUDHUD_AUDIT, tracking_bridge_timeline_v1,
and audit_bridge_entry_v1 from canonical topology definitions.
"""

from __future__ import annotations

import asyncio
import os
import ssl
import sys
from pathlib import Path

import nats
import yaml
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    DiscardPolicy,
    ReplayPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError

REPO_ROOT = Path(__file__).resolve().parents[4]
TOPOLOGY_DIR = REPO_ROOT / "infra" / "eventing" / "topology"

PROOF_STREAMS = frozenset({"HUDHUD_SHIPMENT", "HUDHUD_AUDIT"})
PROOF_CONSUMERS = frozenset({"tracking_bridge_timeline_v1", "audit_bridge_entry_v1"})


def _parse_duration(value: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = value[-1]
    if unit not in units:
        msg = f"unsupported duration unit in {value!r}"
        raise ValueError(msg)
    return float(value[:-1]) * units[unit]


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _stream_config(entry: dict, defaults: dict) -> StreamConfig:
    max_age_seconds = _parse_duration(entry["max_age"])
    return StreamConfig(
        name=entry["name"],
        subjects=entry["subjects"],
        retention=RetentionPolicy.LIMITS,
        max_age=max_age_seconds,
        storage=StorageType.FILE,
        num_replicas=int(defaults.get("num_replicas", 1)),
        discard=DiscardPolicy.OLD,
        max_msg_size=int(defaults.get("max_msg_size_bytes", 262144)),
        duplicate_window=_parse_duration(str(defaults.get("duplicate_window", "2m"))),
        description=entry.get("description", ""),
    )


def _consumer_config(entry: dict, defaults: dict) -> ConsumerConfig:
    backoff = [_parse_duration(item) for item in defaults.get("backoff", [])]
    return ConsumerConfig(
        durable_name=entry["durable_name"],
        filter_subject=entry["filter_subject"],
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=_parse_duration(str(defaults.get("ack_wait", "30s"))),
        max_deliver=int(defaults.get("max_deliver", 5)),
        max_ack_pending=int(defaults.get("max_ack_pending", 100)),
        deliver_policy=DeliverPolicy.ALL,
        replay_policy=ReplayPolicy.INSTANT,
        max_waiting=int(defaults.get("max_waiting", 512)),
        backoff=backoff,
    )


async def ensure_stream(js, config: StreamConfig) -> str:
    try:
        info = await js.stream_info(config.name)
        return f"exists:{config.name}:{info.state.messages}"
    except NotFoundError:
        await js.add_stream(config)
        return f"created:{config.name}"


async def ensure_consumer(js, stream_name: str, config: ConsumerConfig) -> str:
    try:
        info = await js.consumer_info(stream_name, config.durable_name)
        return f"exists:{config.durable_name}:{info.num_pending}"
    except NotFoundError:
        await js.add_consumer(stream_name, config)
        return f"created:{config.durable_name}"


def _build_tls_context(ca_file: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=ca_file)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


async def bootstrap(nats_url: str, *, creds_file: str, ca_file: str) -> list[str]:
    streams_doc = _load_yaml(TOPOLOGY_DIR / "streams.yaml")
    consumers_doc = _load_yaml(TOPOLOGY_DIR / "consumers.yaml")
    stream_defaults = streams_doc.get("defaults", {})
    consumer_defaults = consumers_doc.get("defaults", {})

    stream_entries = [
        entry for entry in streams_doc["streams"] if entry["name"] in PROOF_STREAMS
    ]
    consumer_entries = [
        entry
        for entry in consumers_doc["consumers"]
        if entry["durable_name"] in PROOF_CONSUMERS
    ]
    if len(stream_entries) != len(PROOF_STREAMS):
        msg = "missing required proof streams in canonical topology"
        raise RuntimeError(msg)
    if len(consumer_entries) != len(PROOF_CONSUMERS):
        msg = "missing required proof consumers in canonical topology"
        raise RuntimeError(msg)

    tls = _build_tls_context(ca_file)
    results: list[str] = []
    nc = await nats.connect(
        nats_url,
        user_credentials=creds_file,
        tls=tls,
        connect_timeout=10,
    )
    js = nc.jetstream()

    for entry in stream_entries:
        results.append(await ensure_stream(js, _stream_config(entry, stream_defaults)))

    for entry in consumer_entries:
        results.append(
            await ensure_consumer(
                js,
                entry["stream"],
                _consumer_config(entry, consumer_defaults),
            )
        )

    await nc.drain()
    return results


def main() -> int:
    nats_url = os.environ.get("NATS_URL", "tls://nats:4222")
    creds_file = os.environ.get("NATS_CREDS_FILE")
    ca_file = os.environ.get("NATS_TLS_CA_FILE")
    if not creds_file or not ca_file:
        print("NATS_CREDS_FILE and NATS_TLS_CA_FILE are required", file=sys.stderr)
        return 1
    results = asyncio.run(bootstrap(nats_url, creds_file=creds_file, ca_file=ca_file))
    for line in results:
        print(line)
    print("HUDHUD_NATS_SECURITY_TOPOLOGY_BOOTSTRAPPED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
