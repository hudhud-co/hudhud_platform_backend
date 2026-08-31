"""CWD-independent canonical contract discovery from contracts/events/registry.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import yaml

from legacy_event_bridge.domain.types import ObservationContract


class ContractAssetMissingError(FileNotFoundError):
    """Raised when registry or referenced schema assets are absent."""


@dataclass(frozen=True, slots=True)
class LoadedRegistry:
    contracts_root: Path
    contracts_by_table: dict[str, ObservationContract]
    allowlisted_source_tables: frozenset[str]


def resolve_contracts_root(*, anchor: Path | None = None) -> Path:
    """Locate contracts/events regardless of process working directory."""
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


def _contract_from_entry(contracts_root: Path, entry: dict[str, object]) -> ObservationContract:
    event_type = str(entry["event_type"])
    schema_uri = str(entry["schema_uri"])
    schema_path = contracts_root / "events" / str(entry["schema_path"])
    payload_path = contracts_root / "events" / str(entry["payload_schema_path"])
    for path in (schema_path, payload_path):
        if not path.is_file():
            msg = f"Missing contract schema asset: {path}"
            raise ContractAssetMissingError(msg)
    return ObservationContract(
        event_type=event_type,
        event_version=int(entry["event_version"]),  # type: ignore[arg-type]
        subject=str(entry["subject"]),
        namespace=UUID(str(entry["event_id_namespace"])),
        schema_uri=schema_uri,
    )


@lru_cache(maxsize=1)
def load_bridge_registry(*, anchor: Path | None = None) -> LoadedRegistry:
    """Load A1/A2 observation contracts from the canonical registry."""
    contracts_root = resolve_contracts_root(anchor=anchor)
    registry_path = contracts_root / "events" / "registry.yaml"
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    contracts_by_table: dict[str, ObservationContract] = {}
    allowlisted: set[str] = set()
    for entry in payload["contracts"]:
        event_type = str(entry["event_type"])
        if not event_type.startswith("legacy_bridge.observation."):
            continue
        contract = _contract_from_entry(contracts_root, entry)
        for table in entry.get("source_table_allowlist", []):
            table_name = str(table)
            contracts_by_table[table_name] = contract
            allowlisted.add(table_name)
    if not contracts_by_table:
        msg = "registry.yaml contains no legacy_bridge observation contracts"
        raise ContractAssetMissingError(msg)
    return LoadedRegistry(
        contracts_root=contracts_root,
        contracts_by_table=contracts_by_table,
        allowlisted_source_tables=frozenset(allowlisted),
    )


def reset_registry_cache() -> None:
    load_bridge_registry.cache_clear()
