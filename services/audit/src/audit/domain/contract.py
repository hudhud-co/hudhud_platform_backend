"""Registry-backed A2 contract identity."""

from __future__ import annotations

from audit.infrastructure.contracts.registry import load_a2_registry

_REGISTRY = load_a2_registry().contract

A2_EVENT_TYPE = _REGISTRY.event_type
A2_EVENT_VERSION = _REGISTRY.event_version
A2_SUBJECT = _REGISTRY.subject
A2_STREAM = _REGISTRY.stream
A2_DURABLE_CONSUMER = _REGISTRY.durable_consumer
A2_PRODUCER = _REGISTRY.producer
A2_MESSAGE_KIND = _REGISTRY.message_kind
A2_AGGREGATE_SCOPE = _REGISTRY.aggregate_scope
A2_SOURCE_TABLE = _REGISTRY.source_table
A2_SOURCE_SYSTEM = "legacy"
A2_SCHEMA_URI = _REGISTRY.schema_uri
A2_EVENT_ID_NAMESPACE = _REGISTRY.event_id_namespace

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
    "audit_entry_id",
    "action",
    "entity_type",
    "entity_id",
    "actor_type",
    "source",
    "occurred_at",
    "bridge_mapper_version",
)
