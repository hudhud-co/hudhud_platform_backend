"""Idempotent JetStream bootstrap for HUDHUD_PICKUP + shipment_pickup_facts_v1 only.

Disposable local proof lab — does not mutate other streams or consumers.
Uses canonical topology definitions from infra/eventing/topology/.
"""

from __future__ import annotations

import asyncio
import os
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

PICKUP_STREAM = "HUDHUD_PICKUP"
PICKUP_DURABLE = "shipment_pickup_facts_v1"
PICKUP_FILTER = "hudhud.pickup.pickup.fact.accepted.v1"


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


async def bootstrap(nats_url: str) -> list[str]:
    streams_doc = _load_yaml(TOPOLOGY_DIR / "streams.yaml")
    consumers_doc = _load_yaml(TOPOLOGY_DIR / "consumers.yaml")
    stream_defaults = streams_doc.get("defaults", {})
    consumer_defaults = consumers_doc.get("defaults", {})

    stream_entry = next(
        entry for entry in streams_doc["streams"] if entry["name"] == PICKUP_STREAM
    )
    consumer_entry = next(
        entry
        for entry in consumers_doc["consumers"]
        if entry["durable_name"] == PICKUP_DURABLE
    )
    if consumer_entry["stream"] != PICKUP_STREAM:
        msg = "pickup consumer stream binding mismatch in topology"
        raise RuntimeError(msg)
    if consumer_entry["filter_subject"] != PICKUP_FILTER:
        msg = "pickup consumer filter binding mismatch in topology"
        raise RuntimeError(msg)
    if consumer_defaults.get("ack_policy") != "explicit":
        msg = "canonical AckPolicy must remain explicit"
        raise RuntimeError(msg)

    results: list[str] = []
    nc = await nats.connect(nats_url, connect_timeout=10)
    js = nc.jetstream()

    results.append(await ensure_stream(js, _stream_config(stream_entry, stream_defaults)))
    results.append(
        await ensure_consumer(
            js,
            PICKUP_STREAM,
            _consumer_config(consumer_entry, consumer_defaults),
        )
    )

    await nc.drain()
    return results


def main() -> int:
    nats_url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    results = asyncio.run(bootstrap(nats_url))
    for line in results:
        print(line)
    print("HUDHUD_PICKUP_ACCEPTANCE_EVENTING_TOPOLOGY_BOOTSTRAPPED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
