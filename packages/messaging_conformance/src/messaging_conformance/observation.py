"""Stable UUIDv5 helper for append-only source observations (ADR-0007 / ADR-0008)."""

from __future__ import annotations

from uuid import UUID, uuid5

# Fixed A1/A2 namespaces — authority: contracts/events/registry.yaml
A1_SHIPMENT_TIMELINE_ENTRY_EVENT_ID_NAMESPACE = UUID("5c4b4b77-2b6b-5d2c-bcfd-efea8ce399c3")
A2_AUDIT_ENTRY_EVENT_ID_NAMESPACE = UUID("697097cc-6afb-556b-9f9b-4be135ca6282")

FORBIDDEN_OBSERVATION_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "lsn",
        "xid",
        "timestamp",
        "capture_method",
        "source_op",
        "source_position",
        "transaction_id",
        "wal_position",
    }
)


class ForbiddenObservationIdentityInputError(ValueError):
    """Raised when mutable or provenance fields are supplied as identity inputs."""


def build_append_only_observation_name(
    *,
    source_system: str,
    source_table: str,
    source_pk: str,
) -> str:
    """Build the canonical A1/A2 observation identity string."""
    if not source_system or not source_table or not source_pk:
        msg = "source_system, source_table, and source_pk are required"
        raise ValueError(msg)
    return f"{source_system}:{source_table}:{source_pk}"


def append_only_observation_event_id(
    namespace: UUID,
    *,
    source_system: str,
    source_table: str,
    source_pk: str,
) -> UUID:
    """Deterministic event_id for verified append-only source rows.

    Backfill and CDC INSERT for the same row MUST produce the same UUID.
    LSN, xid, timestamp, capture method, and source_op MUST NOT affect identity.
    """
    name = build_append_only_observation_name(
        source_system=source_system,
        source_table=source_table,
        source_pk=source_pk,
    )
    return uuid5(namespace, name)


def reject_forbidden_observation_identity_fields(fields: dict[str, object]) -> None:
    """Guard helper for adapters that accept arbitrary keyword bags."""
    forbidden = FORBIDDEN_OBSERVATION_IDENTITY_FIELDS.intersection(fields.keys())
    if forbidden:
        joined = ", ".join(sorted(forbidden))
        msg = f"Forbidden append-only observation identity inputs: {joined}"
        raise ForbiddenObservationIdentityInputError(msg)
