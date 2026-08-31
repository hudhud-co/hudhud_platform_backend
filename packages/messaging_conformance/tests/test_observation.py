"""Tests for append-only observation UUIDv5 helper."""

from __future__ import annotations

from uuid import UUID

import pytest

from messaging_conformance import (
    ForbiddenObservationIdentityInputError,
    append_only_observation_event_id,
    build_append_only_observation_name,
    default_observation_namespace_seed,
    reject_forbidden_observation_identity_fields,
)


def test_same_row_identity_is_stable() -> None:
    namespace = default_observation_namespace_seed("shipment.observed")
    first = append_only_observation_event_id(
        namespace,
        source_system="legacy",
        source_table="shipment_events",
        source_pk="123",
    )
    second = append_only_observation_event_id(
        namespace,
        source_system="legacy",
        source_table="shipment_events",
        source_pk="123",
    )
    assert first == second


def test_different_pk_changes_identity() -> None:
    namespace = default_observation_namespace_seed("shipment.observed")
    first = append_only_observation_event_id(
        namespace,
        source_system="legacy",
        source_table="shipment_events",
        source_pk="123",
    )
    second = append_only_observation_event_id(
        namespace,
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


def test_namespace_seed_is_uuid() -> None:
    assert isinstance(default_observation_namespace_seed("audit.observed"), UUID)
