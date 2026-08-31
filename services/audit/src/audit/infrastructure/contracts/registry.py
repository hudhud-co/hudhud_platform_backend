"""CWD-independent canonical contract discovery from contracts/events/registry.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import yaml


class ContractAssetMissingError(FileNotFoundError):
    """Raised when registry or referenced schema assets are absent."""


@dataclass(frozen=True, slots=True)
class A2ContractIdentity:
    event_type: str
    event_version: int
    subject: str
    stream: str
    producer: str
    message_kind: str
    aggregate_scope: str
    source_table: str
    schema_uri: str
    event_id_namespace: UUID
    durable_consumer: str


@dataclass(frozen=True, slots=True)
class LoadedA2Registry:
    contracts_root: Path
    contract: A2ContractIdentity


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
def load_a2_registry(*, anchor: Path | None = None) -> LoadedA2Registry:
    contracts_root = resolve_contracts_root(anchor=anchor)
    registry_path = contracts_root / "events" / "registry.yaml"
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    for entry in payload["contracts"]:
        if entry.get("event_type") != "legacy_bridge.observation.audit_entry":
            continue
        _validate_schema_assets(contracts_root, entry)
        contract = A2ContractIdentity(
            event_type=str(entry["event_type"]),
            event_version=int(entry["event_version"]),  # type: ignore[arg-type]
            subject=str(entry["subject"]),
            stream=str(entry["stream"]),
            producer=str(entry["producer"]),
            message_kind=str(entry["message_kind"]),
            aggregate_scope=str(entry["aggregate_scope"]),
            source_table=str(entry["source_table_allowlist"][0]),
            schema_uri=str(entry["schema_uri"]),
            event_id_namespace=UUID(str(entry["event_id_namespace"])),
            durable_consumer="audit_bridge_entry_v1",
        )
        return LoadedA2Registry(contracts_root=contracts_root, contract=contract)
    msg = "registry.yaml missing legacy_bridge.observation.audit_entry"
    raise ContractAssetMissingError(msg)


def reset_registry_cache() -> None:
    load_a2_registry.cache_clear()
