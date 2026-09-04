"""Registry-backed pickup.fact.accepted contract discovery tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from shipment.domain.contract import (
    PICKUP_ACCEPTED_AGGREGATE_TYPE,
    PICKUP_ACCEPTED_DURABLE_CONSUMER,
    PICKUP_ACCEPTED_EVENT_TYPE,
    PICKUP_ACCEPTED_EVENT_VERSION,
    PICKUP_ACCEPTED_PRODUCER,
    PICKUP_ACCEPTED_SUBJECT,
)
from shipment.infrastructure.contracts.registry import load_pickup_accepted_registry


def test_pickup_accepted_registry_loader_is_cwd_independent() -> None:
    loaded = load_pickup_accepted_registry(
        anchor=Path(__file__).resolve().parents[1] / "src" / "shipment"
    )
    assert loaded.contract.event_type == "pickup.fact.accepted"
    assert loaded.contract.event_version == 1
    assert loaded.contract.subject == "hudhud.pickup.pickup.fact.accepted.v1"
    assert (loaded.contracts_root / "events" / loaded.contract.schema_path).is_file()
    assert (loaded.contracts_root / "events" / loaded.contract.payload_schema_path).is_file()


def test_module_constants_match_registry() -> None:
    registry_path = (
        Path(__file__).resolve().parents[3] / "contracts" / "events" / "registry.yaml"
    )
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    entry = next(c for c in payload["contracts"] if c["event_type"] == "pickup.fact.accepted")
    assert entry["event_type"] == PICKUP_ACCEPTED_EVENT_TYPE
    assert entry["event_version"] == PICKUP_ACCEPTED_EVENT_VERSION
    assert entry["subject"] == PICKUP_ACCEPTED_SUBJECT
    assert entry["producer"] == PICKUP_ACCEPTED_PRODUCER
    assert entry["aggregate_type"] == PICKUP_ACCEPTED_AGGREGATE_TYPE
    assert PICKUP_ACCEPTED_DURABLE_CONSUMER == "shipment_pickup_facts_v1"
