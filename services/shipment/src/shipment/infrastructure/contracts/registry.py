"""CWD-independent canonical contract discovery from contracts/events/registry.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


class ContractAssetMissingError(FileNotFoundError):
    """Raised when registry or referenced schema assets are absent."""


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


@lru_cache(maxsize=1)
def load_pickup_accepted_registry(*, anchor: Path | None = None) -> LoadedPickupAcceptedRegistry:
    contracts_root = resolve_contracts_root(anchor=anchor)
    registry_path = contracts_root / "events" / "registry.yaml"
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    for entry in payload["contracts"]:
        if entry.get("event_type") != "pickup.fact.accepted":
            continue
        if int(entry["event_version"]) != 1:  # type: ignore[arg-type]
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
            durable_consumer="shipment_pickup_facts_v1",
        )
        return LoadedPickupAcceptedRegistry(contracts_root=contracts_root, contract=contract)
    msg = "registry.yaml missing pickup.fact.accepted v1"
    raise ContractAssetMissingError(msg)


def reset_registry_cache() -> None:
    load_pickup_accepted_registry.cache_clear()
