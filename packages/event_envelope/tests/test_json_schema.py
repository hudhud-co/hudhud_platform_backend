"""JSON Schema contract validation tests."""

from __future__ import annotations

import json

import jsonschema
import pytest
from helpers import (
    SCHEMA_PATH,
    aggregate_command,
    aggregate_integration_event,
    load_example,
    non_aggregate_platform_message,
)

from event_envelope import serialize_envelope


@pytest.fixture(scope="module")
def envelope_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture_name",
    [
        "aggregate_command.json",
        "aggregate_integration_event.json",
        "non_aggregate_platform.json",
    ],
)
def test_contract_examples_validate_against_schema(
    envelope_schema: dict,
    fixture_name: str,
) -> None:
    instance = load_example(fixture_name)
    jsonschema.validate(instance=instance, schema=envelope_schema)


def test_serialized_envelopes_validate_against_schema(envelope_schema: dict) -> None:
    for builder in (
        aggregate_command,
        aggregate_integration_event,
        non_aggregate_platform_message,
    ):
        payload = json.loads(serialize_envelope(builder()))
        jsonschema.validate(instance=payload, schema=envelope_schema)
