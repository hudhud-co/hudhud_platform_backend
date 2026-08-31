"""A2 contract identity — ADR-0009 / contracts registry."""

from __future__ import annotations

from uuid import UUID

A2_EVENT_TYPE = "legacy_bridge.observation.audit_entry"
A2_EVENT_VERSION = 1
A2_SUBJECT = "hudhud.audit.legacy_bridge.observation.audit_entry.v1"
A2_STREAM = "HUDHUD_AUDIT"
A2_DURABLE_CONSUMER = "audit_bridge_entry_v1"
A2_PRODUCER = "legacy_bridge"
A2_MESSAGE_KIND = "integration"
A2_AGGREGATE_SCOPE = "non_aggregate"
A2_SOURCE_TABLE = "audit_logs"
A2_SOURCE_SYSTEM = "legacy"
A2_SCHEMA_URI = (
    "https://hudhud.platform/contracts/events/"
    "legacy_bridge.observation.audit_entry/v1.schema.json"
)
A2_EVENT_ID_NAMESPACE = UUID("697097cc-6afb-556b-9f9b-4be135ca6282")

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
