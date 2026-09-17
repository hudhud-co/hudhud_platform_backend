"""CWD-independent canonical contract discovery for Ordering-owned events."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SHIPMENT_REGISTERED_EVENT_TYPE = "order.fact.shipment_registered"
SHIPMENT_REGISTERED_EVENT_VERSION = 1
SHIPMENT_CANCELLED_EVENT_TYPE = "order.fact.shipment_cancelled"
SHIPMENT_CANCELLED_EVENT_VERSION = 1


class ContractAssetMissing(RuntimeError):
    """Registry or referenced schema assets are absent — always fail closed."""


class EnvelopeContractValidationFailed(RuntimeError):
    """A generated envelope does not match its registered schema."""


@dataclass(frozen=True, slots=True)
class OrderingFactContract:
    event_type: str
    event_version: int
    subject: str
    stream: str
    producer: str
    message_kind: str
    aggregate_scope: str
    aggregate_type: str
    schema_uri: str
    schema_path: Path
    payload_schema_path: Path
    envelope_schema_path: Path


@dataclass(frozen=True, slots=True)
class LoadedOrderingFactRegistry:
    contracts_root: Path
    contract: OrderingFactContract
    validator: Draft202012Validator


def resolve_contracts_root(*, anchor: Path | None = None) -> Path:
    """Locate contracts/ regardless of process working directory."""
    env_root = os.environ.get("HUDHUD_CONTRACTS_ROOT")
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if (candidate / "events" / "registry.yaml").is_file():
            return candidate
        msg = f"HUDHUD_CONTRACTS_ROOT missing registry: {candidate}"
        raise ContractAssetMissing(msg)

    start = (anchor or Path(__file__)).resolve()
    for parent in [start, *start.parents]:
        if (parent / "contracts" / "events" / "registry.yaml").is_file():
            return parent / "contracts"

    msg = "contracts/events/registry.yaml not found from repository discovery"
    raise ContractAssetMissing(msg)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        msg = f"Missing contract schema asset: {path}"
        raise ContractAssetMissing(msg)
    return json.loads(path.read_text(encoding="utf-8"))


def _resource_for(path: Path) -> tuple[str, Resource]:
    content = _load_json(path)
    return str(content["$id"]), Resource.from_contents(content)


def _build_validator(contract: OrderingFactContract) -> Draft202012Validator:
    registry = Registry().with_resources(
        [
            _resource_for(contract.envelope_schema_path),
            _resource_for(contract.payload_schema_path),
            _resource_for(contract.schema_path),
        ]
    )
    return Draft202012Validator(_load_json(contract.schema_path), registry=registry)


@lru_cache(maxsize=8)
def load_ordering_fact_registry(
    event_type: str,
    event_version: int,
    *,
    anchor: Path | None = None,
) -> LoadedOrderingFactRegistry:
    """Resolve one registered Ordering contract and build its fail-closed validator."""
    contracts_root = resolve_contracts_root(anchor=anchor)
    payload = yaml.safe_load(
        (contracts_root / "events" / "registry.yaml").read_text(encoding="utf-8")
    )
    for entry in payload["contracts"]:
        if entry.get("event_type") != event_type:
            continue
        if int(entry["event_version"]) != event_version:
            continue
        schema_path = contracts_root / "events" / str(entry["schema_path"])
        payload_schema_path = contracts_root / "events" / str(entry["payload_schema_path"])
        envelope_schema_path = contracts_root / "events" / "envelope" / "v1.schema.json"
        for path in (schema_path, payload_schema_path, envelope_schema_path):
            if not path.is_file():
                msg = f"Missing contract schema asset: {path}"
                raise ContractAssetMissing(msg)
        contract = OrderingFactContract(
            event_type=str(entry["event_type"]),
            event_version=int(entry["event_version"]),
            subject=str(entry["subject"]),
            stream=str(entry["stream"]),
            producer=str(entry["producer"]),
            message_kind=str(entry["message_kind"]),
            aggregate_scope=str(entry["aggregate_scope"]),
            aggregate_type=str(entry["aggregate_type"]),
            schema_uri=str(entry["schema_uri"]),
            schema_path=schema_path,
            payload_schema_path=payload_schema_path,
            envelope_schema_path=envelope_schema_path,
        )
        return LoadedOrderingFactRegistry(
            contracts_root=contracts_root,
            contract=contract,
            validator=_build_validator(contract),
        )
    msg = f"registry.yaml missing {event_type} v{event_version}"
    raise ContractAssetMissing(msg)


def reset_registry_cache() -> None:
    load_ordering_fact_registry.cache_clear()


def validate_envelope(
    instance: dict[str, Any], *, event_type: str, event_version: int
) -> None:
    """Fail closed when the generated envelope does not match the registered schema."""
    loaded = load_ordering_fact_registry(event_type, event_version)
    errors = sorted(loaded.validator.iter_errors(instance), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.path) or "<root>"
        msg = f"{event_type} envelope invalid at {path}: {first.message}"
        raise EnvelopeContractValidationFailed(msg)
