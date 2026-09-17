"""Registry-backed pickup.fact.accepted v1 contract identity."""

from __future__ import annotations

from shipment.infrastructure.contracts.registry import (
    load_pickup_accepted_registry,
    load_pickup_handover_registry,
)

_REGISTRY = load_pickup_accepted_registry().contract
_HANDOVER_REGISTRY = load_pickup_handover_registry().contract

PICKUP_ACCEPTED_EVENT_TYPE = _REGISTRY.event_type
PICKUP_ACCEPTED_EVENT_VERSION = _REGISTRY.event_version
PICKUP_ACCEPTED_SUBJECT = _REGISTRY.subject
PICKUP_ACCEPTED_STREAM = _REGISTRY.stream
PICKUP_ACCEPTED_DURABLE_CONSUMER = _REGISTRY.durable_consumer
PICKUP_ACCEPTED_PRODUCER = _REGISTRY.producer
PICKUP_ACCEPTED_MESSAGE_KIND = _REGISTRY.message_kind
PICKUP_ACCEPTED_AGGREGATE_SCOPE = _REGISTRY.aggregate_scope
PICKUP_ACCEPTED_AGGREGATE_TYPE = _REGISTRY.aggregate_type
PICKUP_ACCEPTED_SCHEMA_URI = _REGISTRY.schema_uri

ALLOWED_OUTCOMES: frozenset[str] = frozenset({"ACCEPTED", "ACCEPTED_WITH_EXCEPTION"})

FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "lsn",
        "xid",
        "txid",
        "source_op",
        "before",
        "after",
        "row_data",
        "raw_row",
        "change_data",
        "op",
        "cdc_payload",
        "password",
        "token",
        "jwt",
        "api_key",
        "secret",
        "inline_evidence",
        "evidence_bytes",
        "exception_evidence",
        "storage_uri",
        "shipment_aggregate_version",
    }
)

REQUIRED_PAYLOAD_FIELDS: tuple[str, ...] = (
    "pickup_task_id",
    "shipment_id",
    "outcome",
    "accepted_at",
    "assigned_driver_user_id",
    "acting_driver_user_id",
    "scanned_identifier",
)

PLACEHOLDER_DRIVER_IDENTITIES: frozenset[str] = frozenset(
    {
        "pickup",
        "producer",
        "unknown",
        "placeholder",
        "n/a",
        "na",
        "none",
        "null",
        "driver",
        "acting_driver",
        "assigned_driver",
    }
)


PICKUP_HANDOVER_EVENT_TYPE = _HANDOVER_REGISTRY.event_type
PICKUP_HANDOVER_EVENT_VERSION = _HANDOVER_REGISTRY.event_version
PICKUP_HANDOVER_SUBJECT = _HANDOVER_REGISTRY.subject
PICKUP_HANDOVER_STREAM = _HANDOVER_REGISTRY.stream
PICKUP_HANDOVER_DURABLE_CONSUMER = _HANDOVER_REGISTRY.durable_consumer
PICKUP_HANDOVER_PRODUCER = _HANDOVER_REGISTRY.producer
PICKUP_HANDOVER_MESSAGE_KIND = _HANDOVER_REGISTRY.message_kind
PICKUP_HANDOVER_AGGREGATE_SCOPE = _HANDOVER_REGISTRY.aggregate_scope
PICKUP_HANDOVER_AGGREGATE_TYPE = _HANDOVER_REGISTRY.aggregate_type
PICKUP_HANDOVER_SCHEMA_URI = _HANDOVER_REGISTRY.schema_uri

#: Only these outcomes release pickup-driver custody. MISSING never appears here.
ALLOWED_HANDOVER_OUTCOMES: frozenset[str] = frozenset(
    {"RECEIVED", "RECEIVED_WITH_DISCREPANCY"}
)

#: MISSING_FROM_DRIVER describes a parcel that never reached the hub, so it can never
#: accompany a custody-releasing fact.
ALLOWED_HANDOVER_DISCREPANCY_REASONS: frozenset[str] = frozenset(
    {"DAMAGED_AT_HANDOVER", "WRONG_HUB_RECEIVED", "OTHER"}
)

REQUIRED_HANDOVER_PAYLOAD_FIELDS: tuple[str, ...] = (
    "pickup_task_id",
    "shipment_id",
    "handover_manifest_id",
    "receiving_hub_id",
    "outcome",
    "released_at",
    "releasing_driver_user_id",
    "receiving_actor_id",
)
