"""Registry-backed A1 contract identity."""

from __future__ import annotations

from tracking.infrastructure.contracts.registry import load_a1_registry

_REGISTRY = load_a1_registry().contract

A1_EVENT_TYPE = _REGISTRY.event_type
A1_EVENT_VERSION = _REGISTRY.event_version
A1_SUBJECT = _REGISTRY.subject
A1_STREAM = _REGISTRY.stream
A1_DURABLE_CONSUMER = _REGISTRY.durable_consumer
A1_PRODUCER = _REGISTRY.producer
A1_MESSAGE_KIND = _REGISTRY.message_kind
A1_AGGREGATE_SCOPE = _REGISTRY.aggregate_scope
A1_SOURCE_TABLE = _REGISTRY.source_table
A1_SOURCE_SYSTEM = "legacy"
A1_SCHEMA_URI = _REGISTRY.schema_uri
A1_EVENT_ID_NAMESPACE = _REGISTRY.event_id_namespace

FORBIDDEN_CDC_PAYLOAD_KEYS: frozenset[str] = frozenset(
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
    }
)

FORBIDDEN_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "token",
        "jwt",
        "api_key",
        "otp",
        "push_token",
        "secret",
        "authorization",
        "national_id",
        "card_number",
        "connection_string",
        "database_url",
    }
)

REQUIRED_PAYLOAD_FIELDS: tuple[str, ...] = (
    "source_table",
    "source_pk",
    "source_position",
    "source_module",
    "legacy_event_type",
    "occurred_at",
    "shipment_id",
    "bridge_mapper_version",
)
