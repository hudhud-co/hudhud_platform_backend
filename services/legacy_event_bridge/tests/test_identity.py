"""Stable observation identity tests."""

from __future__ import annotations

from uuid import UUID

import pytest
from conftest import MAPPER_VERSION, SOURCE_SYSTEM, shipment_change
from messaging_conformance.observation import (
    A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
    ForbiddenObservationIdentityInputError,
    append_only_observation_event_id,
    reject_forbidden_observation_identity_fields,
)

from legacy_event_bridge.application.mapper import build_observation_envelope


def test_stable_replay_identity_same_row() -> None:
    first, _, event_id_first = build_observation_envelope(
        change=shipment_change(source_position="0/AAAA"),
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
    )
    second, _, event_id_second = build_observation_envelope(
        change=shipment_change(source_position="0/BBBB"),
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
    )
    assert event_id_first == event_id_second
    assert first.event_id == second.event_id


def test_forbidden_identity_inputs_rejected() -> None:
    with pytest.raises(ForbiddenObservationIdentityInputError):
        reject_forbidden_observation_identity_fields({"lsn": "0/16B3A8C0"})
    with pytest.raises(ForbiddenObservationIdentityInputError):
        reject_forbidden_observation_identity_fields({"source_op": "insert"})


def test_lsn_does_not_change_event_id() -> None:
    base = append_only_observation_event_id(
        A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
        source_system=SOURCE_SYSTEM,
        source_table="shipment_events",
        source_pk="2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c",
    )
    assert base == append_only_observation_event_id(
        A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE,
        source_system=SOURCE_SYSTEM,
        source_table="shipment_events",
        source_pk=str(UUID("2f9b2c8e-1a3d-4e5f-9b8c-7d6e5f4a3b2c")),
    )
