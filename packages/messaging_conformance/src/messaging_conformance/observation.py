"""Stable UUIDv5 helper for append-only source observations (ADR-0007 / ADR-0008)."""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

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


def default_observation_namespace_seed(domain: str) -> UUID:
    """Derive a stable namespace UUID from a domain/event-type seed string."""
    return uuid5(NAMESPACE_DNS, f"hudhud.observation.{domain}")
