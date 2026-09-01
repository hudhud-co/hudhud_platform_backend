"""Registry-backed A1 contract loading tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import yaml
from messaging_conformance.observation import A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE

from tracking.domain import contract as contract_module
from tracking.infrastructure.contracts.registry import load_a1_registry


def test_a1_registry_loader_is_cwd_independent() -> None:
    loaded = load_a1_registry(anchor=Path(__file__).resolve().parents[1] / "src" / "tracking")
    assert loaded.contracts_root.name == "contracts"
    assert loaded.contract.event_type == "legacy_bridge.observation.shipment_timeline_entry"


def test_module_constants_match_registry() -> None:
    registry_path = (
        Path(__file__).resolve().parents[3] / "contracts" / "events" / "registry.yaml"
    )
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in payload["contracts"]
        if item["event_type"] == "legacy_bridge.observation.shipment_timeline_entry"
    )
    assert entry["event_type"] == contract_module.A1_EVENT_TYPE
    assert entry["subject"] == contract_module.A1_SUBJECT
    assert UUID(entry["event_id_namespace"]) == contract_module.A1_EVENT_ID_NAMESPACE
    assert contract_module.A1_EVENT_ID_NAMESPACE == A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE
