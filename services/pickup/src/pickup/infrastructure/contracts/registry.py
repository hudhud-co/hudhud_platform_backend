"""CWD-independent canonical contract discovery for pickup.fact.accepted."""

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

from pickup.domain.errors import ContractAssetMissing, EnvelopeContractValidationFailed

EVENT_TYPE = "pickup.fact.accepted"
EVENT_VERSION = 1


class ContractAssetMissingError(ContractAssetMissing):
    """Raised when registry or referenced schema assets are absent."""


@dataclass(frozen=True, slots=True)
class AcceptedFactContract:
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
class LoadedAcceptedFactRegistry:
    contracts_root: Path
    contract: AcceptedFactContract
    validator: Draft202012Validator


def resolve_contracts_root(*, anchor: Path | None = None) -> Path:
    """Locate contracts/ regardless of process working directory."""
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


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        msg = f"Missing contract schema asset: {path}"
        raise ContractAssetMissingError(msg)
    return json.loads(path.read_text(encoding="utf-8"))


def _resource_for(path: Path) -> tuple[str, Resource]:
    content = _load_json(path)
    return str(content["$id"]), Resource.from_contents(content)


def _build_validator(contract: AcceptedFactContract) -> Draft202012Validator:
    resources = [
        _resource_for(contract.envelope_schema_path),
        _resource_for(contract.payload_schema_path),
        _resource_for(contract.schema_path),
    ]
    registry = Registry().with_resources(resources)
    schema = _load_json(contract.schema_path)
    return Draft202012Validator(schema, registry=registry)


@lru_cache(maxsize=1)
def load_accepted_fact_registry(*, anchor: Path | None = None) -> LoadedAcceptedFactRegistry:
    contracts_root = resolve_contracts_root(anchor=anchor)
    registry_path = contracts_root / "events" / "registry.yaml"
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    for entry in payload["contracts"]:
        if entry.get("event_type") != EVENT_TYPE:
            continue
        if int(entry["event_version"]) != EVENT_VERSION:
            continue
        schema_path = contracts_root / "events" / str(entry["schema_path"])
        payload_schema_path = contracts_root / "events" / str(entry["payload_schema_path"])
        envelope_schema_path = contracts_root / "events" / "envelope" / "v1.schema.json"
        for path in (schema_path, payload_schema_path, envelope_schema_path):
            if not path.is_file():
                msg = f"Missing contract schema asset: {path}"
                raise ContractAssetMissingError(msg)
        contract = AcceptedFactContract(
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
        return LoadedAcceptedFactRegistry(
            contracts_root=contracts_root,
            contract=contract,
            validator=_build_validator(contract),
        )
    msg = "registry.yaml missing pickup.fact.accepted v1"
    raise ContractAssetMissingError(msg)


def reset_registry_cache() -> None:
    load_accepted_fact_registry.cache_clear()


def validate_accepted_fact_envelope(instance: dict[str, Any]) -> None:
    """Fail closed when the generated envelope does not match the registered schema."""
    loaded = load_accepted_fact_registry()
    errors = sorted(loaded.validator.iter_errors(instance), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.path) or "<root>"
        msg = f"pickup.fact.accepted envelope invalid at {path}: {first.message}"
        raise EnvelopeContractValidationFailed(msg)
