"""Tests for append-only observation UUIDv5 helper."""

from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid5

import pytest
import yaml

from messaging_conformance import (
    A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
    A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE,
    ForbiddenObservationIdentityInputError,
    append_only_observation_event_id,
    build_append_only_observation_name,
    reject_forbidden_observation_identity_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "contracts" / "events" / "registry.yaml"


def test_same_row_identity_is_stable() -> None:
    first = append_only_observation_event_id(
        A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
        source_system="legacy",
        source_table="shipment_events",
        source_pk="123",
    )
    second = append_only_observation_event_id(
        A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
        source_system="legacy",
        source_table="shipment_events",
        source_pk="123",
    )
    assert first == second


def test_different_pk_changes_identity() -> None:
    first = append_only_observation_event_id(
        A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
        source_system="legacy",
        source_table="shipment_events",
        source_pk="123",
    )
    second = append_only_observation_event_id(
        A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
        source_system="legacy",
        source_table="shipment_events",
        source_pk="124",
    )
    assert first != second


def test_name_format_is_source_system_table_pk() -> None:
    assert (
        build_append_only_observation_name(
            source_system="legacy",
            source_table="shipment_events",
            source_pk="42",
        )
        == "legacy:shipment_events:42"
    )


def test_forbidden_identity_fields_are_rejected() -> None:
    with pytest.raises(ForbiddenObservationIdentityInputError):
        reject_forbidden_observation_identity_fields({"lsn": "0/16B3748"})
    with pytest.raises(ForbiddenObservationIdentityInputError):
        reject_forbidden_observation_identity_fields({"source_op": "INSERT"})


def test_registry_namespaces_match_public_constants() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    by_type = {entry["event_type"]: entry for entry in registry["contracts"]}
    a1_registry = UUID(
        by_type["legacy_bridge.observation.shipment_timeline_entry"]["event_id_namespace"]
    )
    a2_registry = UUID(by_type["legacy_bridge.observation.audit_entry"]["event_id_namespace"])
    assert a1_registry == A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE
    assert a2_registry == A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE


def test_registry_namespaces_match_documented_derivation() -> None:
    assert (
        uuid5(
            NAMESPACE_DNS,
            "hudhud.platform/events/legacy_bridge.observation.shipment_timeline_entry/v1",
        )
        == A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE
    )
    assert (
        uuid5(
            NAMESPACE_DNS,
            "hudhud.platform/events/legacy_bridge.observation.audit_entry/v1",
        )
        == A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE
    )
