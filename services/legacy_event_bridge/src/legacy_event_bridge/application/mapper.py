"""A1/A2 observation mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from event_envelope.enums import AggregateScope, DataClassification, MessageKind
from event_envelope.envelope import EventEnvelope
from event_envelope.serde import envelope_to_json_dict
from messaging_conformance.observation import (
    append_only_observation_event_id,
    reject_forbidden_observation_identity_fields,
)

from legacy_event_bridge.domain.errors import MappingError, SourceTableNotAllowedError
from legacy_event_bridge.domain.sanitize import sanitize_error_message
from legacy_event_bridge.domain.types import (
    CdcChange,
    LandingRecord,
    ObservationContract,
    allowlisted_source_tables,
    contract_by_table,
)


def assert_source_table_allowed(source_table: str) -> None:
    if source_table not in allowlisted_source_tables():
        msg = f"Source table not allowlisted: {source_table}"
        raise SourceTableNotAllowedError(msg)


def contract_for_table(source_table: str) -> ObservationContract:
    assert_source_table_allowed(source_table)
    return contract_by_table()[source_table]


def build_observation_payload(
    *,
    change: CdcChange | LandingRecord,
    mapper_version: str,
    source_system: str,
) -> dict[str, Any]:
    assert_source_table_allowed(
        change.source_table if isinstance(change, CdcChange) else change.identity.source_table
    )
    fields = (
        change.normalized_fields
        if isinstance(change, CdcChange)
        else change.normalized_fields
    )
    reject_forbidden_observation_identity_fields(dict(fields))
    source_table = (
        change.source_table if isinstance(change, CdcChange) else change.identity.source_table
    )
    source_pk = change.source_pk if isinstance(change, CdcChange) else change.identity.source_pk
    source_position = (
        change.source_position if isinstance(change, CdcChange) else change.source_position
    )

    base: dict[str, Any] = {
        "source_table": source_table,
        "source_pk": str(source_pk),
        "source_position": source_position,
        "source_module": fields["source_module"],
        "bridge_mapper_version": mapper_version,
    }

    if source_table == "shipment_events":
        base.update(
            {
                "legacy_event_type": fields["legacy_event_type"],
                "occurred_at": _format_occurred_at(fields["occurred_at"]),
                "shipment_id": str(fields["shipment_id"]),
            }
        )
        for optional in ("old_status", "new_status", "actor_type", "actor_id", "metadata"):
            if optional in fields and fields[optional] is not None:
                base[optional] = fields[optional]
    elif source_table == "audit_logs":
        base.update(
            {
                "audit_entry_id": str(source_pk),
                "action": fields["action"],
                "entity_type": fields["entity_type"],
                "entity_id": str(fields["entity_id"]),
                "actor_type": fields["actor_type"],
                "source": fields["source"],
                "occurred_at": _format_occurred_at(fields["occurred_at"]),
            }
        )
        if fields.get("actor_id") is not None:
            base["actor_id"] = str(fields["actor_id"])
        if fields.get("metadata") is not None:
            base["metadata"] = fields["metadata"]

    _ = source_system  # identity derivation uses explicit source_system at envelope build
    return base


def build_observation_envelope(
    *,
    change: CdcChange | LandingRecord,
    mapper_version: str,
    source_system: str,
) -> tuple[EventEnvelope, ObservationContract, UUID]:
    if isinstance(change, CdcChange):
        source_table = change.source_table
        source_pk = change.source_pk
    else:
        source_table = change.identity.source_table
        source_pk = change.identity.source_pk
    contract = contract_for_table(source_table)

    event_id = append_only_observation_event_id(
        contract.namespace,
        source_system=source_system,
        source_table=source_table,
        source_pk=str(source_pk),
    )
    payload = build_observation_payload(
        change=change,
        mapper_version=mapper_version,
        source_system=source_system,
    )
    occurred_at = _parse_occurred_at(payload["occurred_at"])

    envelope = EventEnvelope(
        event_id=event_id,
        event_type=contract.event_type,
        event_version=contract.event_version,
        occurred_at=occurred_at,
        producer="legacy_bridge",
        message_kind=MessageKind.INTEGRATION,
        aggregate_scope=AggregateScope.NON_AGGREGATE,
        aggregate_type=None,
        aggregate_id=None,
        aggregate_version=None,
        correlation_id=event_id,
        data_classification=DataClassification.INTERNAL,
        pii_present=False,
        schema_uri=contract.schema_uri,
        payload=payload,
        metadata={"replay": False},
    )
    return envelope, contract, event_id


def map_landing_to_outbox_payload(
    landing: LandingRecord,
    *,
    mapper_version: str,
    source_system: str,
) -> tuple[dict[str, Any], str, UUID]:
    try:
        envelope, contract, event_id = build_observation_envelope(
            change=landing,
            mapper_version=mapper_version,
            source_system=source_system,
        )
    except (KeyError, TypeError, ValueError) as exc:
        msg = sanitize_error_message(str(exc))
        raise MappingError(msg) from exc
    return envelope_to_json_dict(envelope), contract.subject, event_id


def _format_occurred_at(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return str(value)


def _parse_occurred_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def new_correlation_id() -> UUID:
    return uuid4()
