"""Compatibility tests for ADR-0009 legacy bridge observation contracts."""

from __future__ import annotations

import uuid

import pytest
from helpers import (
    A1_DIR,
    A2_DIR,
    a1_validator,
    a2_validator,
    load_example,
    load_fixture,
    with_additive_payload_field,
)
from jsonschema.exceptions import ValidationError

A1_EVENT_ID_NAMESPACE = uuid.UUID("5c4b4b77-2b6b-5d2c-bcfd-efea8ce399c3")
A2_EVENT_ID_NAMESPACE = uuid.UUID("697097cc-6afb-556b-9f9b-4be135ca6282")
SOURCE_SYSTEM = "legacy"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "complete_envelope.json",
        "minimal_valid.json",
        "redacted_representative.json",
    ],
)
def test_a1_valid_examples_validate(fixture_name: str) -> None:
    instance = load_example(A1_DIR, fixture_name)
    a1_validator().validate(instance)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "invalid_wrong_producer.json",
        "invalid_invented_aggregate_version.json",
        "invalid_wrong_source_table.json",
        "invalid_raw_cdc_fields.json",
    ],
)
def test_a1_invalid_fixtures_rejected(fixture_name: str) -> None:
    instance = load_fixture(A1_DIR, fixture_name)
    with pytest.raises(ValidationError):
        a1_validator().validate(instance)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "complete_envelope.json",
        "minimal_valid.json",
        "redacted_representative.json",
    ],
)
def test_a2_valid_examples_validate(fixture_name: str) -> None:
    instance = load_example(A2_DIR, fixture_name)
    a2_validator().validate(instance)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "invalid_canonical_audit_event_type.json",
        "invalid_wrong_source_table.json",
        "invalid_secret_in_metadata.json",
    ],
)
def test_a2_invalid_fixtures_rejected(fixture_name: str) -> None:
    instance = load_fixture(A2_DIR, fixture_name)
    with pytest.raises(ValidationError):
        a2_validator().validate(instance)


def test_a1_optional_declared_fields_are_compatible() -> None:
    minimal = load_example(A1_DIR, "minimal_valid.json")
    with_optional = with_additive_payload_field(minimal, "old_status", "CREATED")
    with_optional = with_additive_payload_field(with_optional, "new_status", "IN_CUSTODY")
    a1_validator().validate(with_optional)


def test_a2_optional_declared_fields_are_compatible() -> None:
    minimal = load_example(A2_DIR, "minimal_valid.json")
    with_optional = with_additive_payload_field(
        minimal,
        "actor_id",
        "c0ffee00-0000-4000-8000-000000000001",
    )
    a2_validator().validate(with_optional)


def test_a1_event_id_uuidv5_identity_formula() -> None:
    source_pk = "2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c"
    name = f"{SOURCE_SYSTEM}:shipment_events:{source_pk}"
    expected = uuid.uuid5(A1_EVENT_ID_NAMESPACE, name)
    example = load_example(A1_DIR, "complete_envelope.json")
    assert example["event_id"] == str(expected)


def test_a2_event_id_uuidv5_identity_formula() -> None:
    source_pk = "8d2e1f0a-9b8c-7d6e-5f4a-3b2c1d0e9f8a"
    name = f"{SOURCE_SYSTEM}:audit_logs:{source_pk}"
    expected = uuid.uuid5(A2_EVENT_ID_NAMESPACE, name)
    example = load_example(A2_DIR, "complete_envelope.json")
    assert example["event_id"] == str(expected)


def test_a1_unknown_top_level_payload_field_rejected_at_publish() -> None:
    instance = load_example(A1_DIR, "minimal_valid.json")
    instance["payload"]["unexpected_field"] = "not-allowed"
    with pytest.raises(ValidationError):
        a1_validator().validate(instance)
