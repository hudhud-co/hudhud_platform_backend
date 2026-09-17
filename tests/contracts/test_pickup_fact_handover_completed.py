"""Contract conformance for pickup.fact.handover_completed v1 (ADR-0009 C11)."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml
from helpers import (
    C11_DIR,
    REPO_ROOT,
    c11_validator,
    load_example,
    load_fixture,
)

REGISTRY_PATH = REPO_ROOT / "contracts" / "events" / "registry.yaml"
EVENT_TYPE = "pickup.fact.handover_completed"

EXAMPLES = (
    "minimal_valid.json",
    "complete_envelope.json",
    "received_with_discrepancy.json",
)

FIXTURES = (
    "invalid_missing_outcome.json",
    "invalid_discrepancy_without_reason.json",
    "invalid_discrepancy_without_media_refs.json",
    "invalid_missing_from_driver_reason.json",
    "invalid_shipment_aggregate.json",
    "invalid_missing_aggregate_version.json",
    "invalid_wrong_producer.json",
    "invalid_raw_cdc_fields.json",
    "invalid_shipment_aggregate_version.json",
)


def _registry_entry() -> dict:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = [item for item in registry["contracts"] if item["event_type"] == EVENT_TYPE]
    assert len(entries) == 1
    return entries[0]


def test_registry_declares_the_pickup_owned_aggregate_contract() -> None:
    entry = _registry_entry()
    assert entry["producer"] == "pickup"
    assert entry["aggregate_scope"] == "aggregate"
    assert entry["aggregate_type"] == "pickup_task"
    assert entry["subject"] == "hudhud.pickup.pickup.fact.handover_completed.v1"
    assert entry["stream"] == "HUDHUD_PICKUP"
    assert entry["adr"] == "ADR-0009-C11"


def test_registry_does_not_claim_production_enablement() -> None:
    entry = _registry_entry()
    assert entry["implementation_status"] == (
        "implementation_authorized_not_production_enabled"
    )


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_validate(name: str) -> None:
    errors = list(c11_validator().iter_errors(load_example(C11_DIR, name)))
    assert errors == []


@pytest.mark.parametrize("name", FIXTURES)
def test_fixtures_are_rejected(name: str) -> None:
    errors = list(c11_validator().iter_errors(load_fixture(C11_DIR, name)))
    assert errors, f"{name} must be rejected by the contract"


def test_a_missing_parcel_can_never_release_custody_through_this_event() -> None:
    instance = load_example(C11_DIR, "minimal_valid.json")
    instance["payload"]["outcome"] = "MISSING"
    assert list(c11_validator().iter_errors(instance))


def test_payload_rejects_unknown_fields() -> None:
    instance = deepcopy(load_example(C11_DIR, "minimal_valid.json"))
    instance["payload"]["driver_cash_balance"] = "100"
    assert list(c11_validator().iter_errors(instance))


def test_aggregate_id_and_payload_task_id_describe_one_pickup_task() -> None:
    instance = load_example(C11_DIR, "complete_envelope.json")
    assert instance["aggregate_id"] == instance["payload"]["pickup_task_id"]
    assert instance["aggregate_id"] != instance["payload"]["shipment_id"]
