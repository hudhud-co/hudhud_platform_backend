"""Registry-backed A2 contract loading tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import yaml
from messaging_conformance.observation import A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE

from audit.domain import contract as contract_module
from audit.infrastructure.contracts.registry import load_a2_registry


def test_a2_registry_loader_is_cwd_independent() -> None:
    loaded = load_a2_registry(anchor=Path(__file__).resolve().parents[1] / "src" / "audit")
    assert loaded.contracts_root.name == "contracts"
    assert loaded.contract.event_type == "legacy_bridge.observation.audit_entry"


def test_module_constants_match_registry() -> None:
    registry_path = (
        Path(__file__).resolve().parents[3] / "contracts" / "events" / "registry.yaml"
    )
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in payload["contracts"]
        if item["event_type"] == "legacy_bridge.observation.audit_entry"
    )
    assert entry["event_type"] == contract_module.A2_EVENT_TYPE
    assert entry["subject"] == contract_module.A2_SUBJECT
    assert UUID(entry["event_id_namespace"]) == contract_module.A2_EVENT_ID_NAMESPACE
    assert contract_module.A2_EVENT_ID_NAMESPACE == A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE
