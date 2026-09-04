"""Compatibility tests for ADR-0009 C10 pickup.fact.accepted v1."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml
from helpers import (
    C10_DIR,
    CONTRACTS_ROOT,
    c10_validator,
    load_example,
    load_fixture,
)
from jsonschema.exceptions import ValidationError

REGISTRY_PATH = CONTRACTS_ROOT / "registry.yaml"


def _registry_entry() -> dict:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    by_type = {entry["event_type"]: entry for entry in registry["contracts"]}
    return by_type["pickup.fact.accepted"]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "complete_envelope.json",
        "minimal_valid.json",
        "accepted_with_exception.json",
    ],
)
def test_c10_valid_examples_validate(fixture_name: str) -> None:
    instance = load_example(C10_DIR, fixture_name)
    c10_validator().validate(instance)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "invalid_wrong_producer.json",
        "invalid_rejected_outcome.json",
        "invalid_shipment_aggregate.json",
        "invalid_shipment_aggregate_version.json",
        "invalid_raw_cdc_fields.json",
        "invalid_missing_aggregate_version.json",
    ],
)
def test_c10_invalid_fixtures_rejected(fixture_name: str) -> None:
    instance = load_fixture(C10_DIR, fixture_name)
    with pytest.raises(ValidationError):
        c10_validator().validate(instance)


def test_c10_unknown_top_level_payload_field_rejected_at_publish() -> None:
    instance = load_example(C10_DIR, "minimal_valid.json")
    instance["payload"]["unexpected_field"] = "not-allowed"
    with pytest.raises(ValidationError):
        c10_validator().validate(instance)


def test_c10_aggregate_id_matches_pickup_task_id() -> None:
    instance = load_example(C10_DIR, "minimal_valid.json")
    assert instance["aggregate_id"] == instance["payload"]["pickup_task_id"]
    assert instance["aggregate_type"] == "pickup_task"


def test_c10_outbox_retry_reuses_stable_event_id() -> None:
    """Outbox relay retries must republish the same event_id (identity stability)."""
    first = load_example(C10_DIR, "minimal_valid.json")
    retry = deepcopy(first)
    retry["published_at"] = "2026-09-04T12:00:05.000Z"
    assert retry["event_id"] == first["event_id"]
    c10_validator().validate(retry)


def test_c10_registry_identity_and_routing() -> None:
    entry = _registry_entry()
    assert entry["event_version"] == 1
    assert entry["producer"] == "pickup"
    assert entry["message_kind"] == "integration"
    assert entry["aggregate_scope"] == "aggregate"
    assert entry["aggregate_type"] == "pickup_task"
    assert entry["subject"] == "hudhud.pickup.pickup.fact.accepted.v1"
    assert entry["stream"] == "HUDHUD_PICKUP"
    assert entry["schema_uri"] == (
        "https://hudhud.platform/contracts/events/pickup.fact.accepted/v1.schema.json"
    )
    assert entry["adr"] == "ADR-0009-C10"
    assert entry["implementation_status"] == "implementation_authorized_not_production_enabled"
    assert "event_id_namespace" not in entry


def test_c10_a1_a2_registry_entries_unchanged() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    by_type = {entry["event_type"]: entry for entry in registry["contracts"]}
    assert by_type["legacy_bridge.observation.shipment_timeline_entry"]["event_id_namespace"] == (
        "5c4b4b77-2b6b-5d2c-bcfd-efea8ce399c3"
    )
    assert by_type["legacy_bridge.observation.audit_entry"]["event_id_namespace"] == (
        "697097cc-6afb-556b-9f9b-4be135ca6282"
    )
    assert by_type["legacy_bridge.observation.shipment_timeline_entry"]["adr"] == "ADR-0009-A1"
    assert by_type["legacy_bridge.observation.audit_entry"]["adr"] == "ADR-0009-A2"
