"""CWD-independent canonical contract discovery from contracts/events/registry.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


class ContractAssetMissingError(FileNotFoundError):
    """Raised when registry or referenced schema assets are absent."""


ACCEPTED_EVENT_TYPE = "pickup.fact.accepted"
HANDOVER_COMPLETED_EVENT_TYPE = "pickup.fact.handover_completed"

#: Durable consumer names are Shipment-owned and stable across redeploys (ADR-0008).
DURABLE_CONSUMERS: dict[str, str] = {
    ACCEPTED_EVENT_TYPE: "shipment_pickup_facts_v1",
    HANDOVER_COMPLETED_EVENT_TYPE: "shipment_pickup_handover_facts_v1",
}


@dataclass(frozen=True, slots=True)
class PickupAcceptedContractIdentity:
    event_type: str
    event_version: int
    subject: str
    stream: str
    producer: str
    message_kind: str
    aggregate_scope: str
    aggregate_type: str
    schema_uri: str
    schema_path: str
    payload_schema_path: str
    durable_consumer: str


@dataclass(frozen=True, slots=True)
class LoadedPickupAcceptedRegistry:
    contracts_root: Path
    contract: PickupAcceptedContractIdentity


def resolve_contracts_root(*, anchor: Path | None = None) -> Path:
    env_root = os.environ.get("HUDHUD_CONTRACTS_ROOT")
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        registry = candidate / "events" / "registry.yaml"
        if registry.is_file():
            return candidate
        msg = f"HUDHUD_CONTRACTS_ROOT missing registry: {registry}"
        raise ContractAssetMissingError(msg)

    start = (anchor or Path(__file__)).resolve()
    for parent in [start, *start.parents]:
        contracts_root = parent / "contracts"
        registry = contracts_root / "events" / "registry.yaml"
        if registry.is_file():
            return contracts_root

    msg = "contracts/events/registry.yaml not found from repository discovery"
    raise ContractAssetMissingError(msg)


def _validate_schema_assets(contracts_root: Path, entry: dict[str, object]) -> None:
    for key in ("schema_path", "payload_schema_path"):
        path = contracts_root / "events" / str(entry[key])
        if not path.is_file():
            msg = f"Missing contract schema asset: {path}"
            raise ContractAssetMissingError(msg)


@lru_cache(maxsize=4)
def load_pickup_fact_registry(
    event_type: str,
    event_version: int = 1,
    *,
    anchor: Path | None = None,
) -> LoadedPickupAcceptedRegistry:
    """Resolve one registered Pickup contract Shipment consumes."""
    contracts_root = resolve_contracts_root(anchor=anchor)
    registry_path = contracts_root / "events" / "registry.yaml"
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    for entry in payload["contracts"]:
        if entry.get("event_type") != event_type:
            continue
        if int(entry["event_version"]) != event_version:  # type: ignore[arg-type]
            continue
        _validate_schema_assets(contracts_root, entry)
        contract = PickupAcceptedContractIdentity(
            event_type=str(entry["event_type"]),
            event_version=int(entry["event_version"]),  # type: ignore[arg-type]
            subject=str(entry["subject"]),
            stream=str(entry["stream"]),
            producer=str(entry["producer"]),
            message_kind=str(entry["message_kind"]),
            aggregate_scope=str(entry["aggregate_scope"]),
            aggregate_type=str(entry["aggregate_type"]),
            schema_uri=str(entry["schema_uri"]),
            schema_path=str(entry["schema_path"]),
            payload_schema_path=str(entry["payload_schema_path"]),
            durable_consumer=DURABLE_CONSUMERS[event_type],
        )
        return LoadedPickupAcceptedRegistry(contracts_root=contracts_root, contract=contract)
    msg = f"registry.yaml missing {event_type} v{event_version}"
    raise ContractAssetMissingError(msg)


def load_pickup_accepted_registry(*, anchor: Path | None = None) -> LoadedPickupAcceptedRegistry:
    return load_pickup_fact_registry(ACCEPTED_EVENT_TYPE, 1, anchor=anchor)


def load_pickup_handover_registry(*, anchor: Path | None = None) -> LoadedPickupAcceptedRegistry:
    return load_pickup_fact_registry(HANDOVER_COMPLETED_EVENT_TYPE, 1, anchor=anchor)


def reset_registry_cache() -> None:
    load_pickup_fact_registry.cache_clear()
