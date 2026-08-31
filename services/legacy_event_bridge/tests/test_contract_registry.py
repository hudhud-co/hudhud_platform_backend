"""Registry-backed contract loading tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import yaml
from messaging_conformance.observation import (
    A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
    A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE,
)

from legacy_event_bridge.domain.types import contract_by_table
from legacy_event_bridge.infrastructure.contracts.registry import load_bridge_registry


def test_registry_loader_is_cwd_independent() -> None:
    loaded = load_bridge_registry(
        anchor=Path(__file__).resolve().parents[1] / "src" / "legacy_event_bridge"
    )
    assert loaded.contracts_root.name == "contracts"
    assert "shipment_events" in loaded.contracts_by_table
    assert "audit_logs" in loaded.contracts_by_table


def test_contract_by_table_matches_registry_namespaces() -> None:
    registry_path = (
        Path(__file__).resolve().parents[3] / "contracts" / "events" / "registry.yaml"
    )
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    by_type = {entry["event_type"]: entry for entry in payload["contracts"]}
    contracts = contract_by_table()
    a1 = contracts["shipment_events"]
    a2 = contracts["audit_logs"]
    assert a1.namespace == UUID(
        by_type["legacy_bridge.observation.shipment_timeline_entry"]["event_id_namespace"]
    )
    assert a2.namespace == UUID(
        by_type["legacy_bridge.observation.audit_entry"]["event_id_namespace"]
    )
    assert a1.namespace == A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE
    assert a2.namespace == A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE
