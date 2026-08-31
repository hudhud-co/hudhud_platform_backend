"""A1 and A2 mapping contract tests."""

from __future__ import annotations

from uuid import UUID

from conftest import MAPPER_VERSION, SOURCE_SYSTEM, audit_change, shipment_change
from event_envelope.enums import AggregateScope, MessageKind
from messaging_conformance.observation import (
    A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
    A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE,
    append_only_observation_event_id,
)

from legacy_event_bridge.application.mapper import build_observation_envelope


def test_a1_subject_namespace_and_non_aggregate() -> None:
    envelope, contract, event_id = build_observation_envelope(
        change=shipment_change(),
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
    )
    assert contract.event_type == "legacy_bridge.observation.shipment_timeline_entry"
    assert contract.subject == (
        "hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1"
    )
    assert contract.namespace == A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE
    assert envelope.producer == "legacy_bridge"
    assert envelope.message_kind is MessageKind.INTEGRATION
    assert envelope.aggregate_scope is AggregateScope.NON_AGGREGATE
    assert envelope.aggregate_version is None
    assert event_id == append_only_observation_event_id(
        A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
        source_system=SOURCE_SYSTEM,
        source_table="shipment_events",
        source_pk="2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c",
    )


def test_a2_subject_namespace_and_non_aggregate() -> None:
    envelope, contract, event_id = build_observation_envelope(
        change=audit_change(),
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
    )
    assert contract.event_type == "legacy_bridge.observation.audit_entry"
    assert contract.subject == "hudhud.audit.legacy_bridge.observation.audit_entry.v1"
    assert contract.namespace == A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE
    assert envelope.producer == "legacy_bridge"
    assert envelope.message_kind is MessageKind.INTEGRATION
    assert envelope.aggregate_scope is AggregateScope.NON_AGGREGATE
    assert envelope.aggregate_version is None
    assert envelope.event_type != "audit.fact.entry_recorded"
    assert event_id == append_only_observation_event_id(
        A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE,
        source_system=SOURCE_SYSTEM,
        source_table="audit_logs",
        source_pk="8d2e1f0a-9b8c-7d6e-5f4a-3b2c1d0e9f8a",
    )


def test_a1_payload_required_fields() -> None:
    envelope, _, _ = build_observation_envelope(
        change=shipment_change(),
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
    )
    payload = envelope.payload
    assert payload["source_table"] == "shipment_events"
    assert payload["bridge_mapper_version"] == MAPPER_VERSION
    assert UUID(payload["shipment_id"]) == UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
