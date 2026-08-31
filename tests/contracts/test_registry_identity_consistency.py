"""Cross-artifact consistency for A1/A2 fixed event_id namespaces."""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "contracts" / "events" / "registry.yaml"

A1_DERIVATION_SEED = (
    "hudhud.platform/events/legacy_bridge.observation.shipment_timeline_entry/v1"
)
A2_DERIVATION_SEED = "hudhud.platform/events/legacy_bridge.observation.audit_entry/v1"


def _registry_by_event_type() -> dict[str, dict]:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {entry["event_type"]: entry for entry in registry["contracts"]}


def test_registry_assigns_fixed_a1_a2_namespaces() -> None:
    by_type = _registry_by_event_type()
    assert by_type["legacy_bridge.observation.shipment_timeline_entry"]["event_id_namespace"] == (
        "5c4b4b77-2b6b-5d2c-bcfd-efea8ce399c3"
    )
    assert by_type["legacy_bridge.observation.audit_entry"]["event_id_namespace"] == (
        "697097cc-6afb-556b-9f9b-4be135ca6282"
    )


def test_registry_namespaces_match_documented_uuidv5_derivation() -> None:
    by_type = _registry_by_event_type()
    a1 = uuid.UUID(
        by_type["legacy_bridge.observation.shipment_timeline_entry"]["event_id_namespace"]
    )
    a2 = uuid.UUID(by_type["legacy_bridge.observation.audit_entry"]["event_id_namespace"])
    assert a1 == uuid.uuid5(uuid.NAMESPACE_DNS, A1_DERIVATION_SEED)
    assert a2 == uuid.uuid5(uuid.NAMESPACE_DNS, A2_DERIVATION_SEED)
