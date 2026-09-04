"""Shared helpers for JSON Schema contract tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = REPO_ROOT / "contracts" / "events"

A1_DIR = CONTRACTS_ROOT / "legacy_bridge.observation.shipment_timeline_entry"
A2_DIR = CONTRACTS_ROOT / "legacy_bridge.observation.audit_entry"
C10_DIR = CONTRACTS_ROOT / "pickup.fact.accepted"
ENVELOPE_SCHEMA_PATH = CONTRACTS_ROOT / "envelope" / "v1.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resource_for(path: Path) -> tuple[str, Resource]:
    content = load_json(path)
    schema_id = content["$id"]
    return schema_id, Resource.from_contents(content)


def build_validator(schema_path: Path, *extra_schema_paths: Path) -> Draft202012Validator:
    paths = (ENVELOPE_SCHEMA_PATH, *extra_schema_paths, schema_path)
    resources = [_resource_for(path) for path in paths]
    registry = Registry().with_resources(resources)
    schema = load_json(schema_path)
    return Draft202012Validator(schema, registry=registry)


def a1_validator() -> Draft202012Validator:
    return build_validator(
        A1_DIR / "v1.schema.json",
        A1_DIR / "v1.payload.schema.json",
    )


def a2_validator() -> Draft202012Validator:
    return build_validator(
        A2_DIR / "v1.schema.json",
        A2_DIR / "v1.payload.schema.json",
    )


def c10_validator() -> Draft202012Validator:
    return build_validator(
        C10_DIR / "v1.schema.json",
        C10_DIR / "v1.payload.schema.json",
    )


def load_example(contract_dir: Path, name: str) -> dict:
    return load_json(contract_dir / "examples" / name)


def load_fixture(contract_dir: Path, name: str) -> dict:
    return load_json(contract_dir / "fixtures" / name)


def with_additive_payload_field(instance: dict, field: str, value: object) -> dict:
    copy = deepcopy(instance)
    copy["payload"][field] = value
    return copy
