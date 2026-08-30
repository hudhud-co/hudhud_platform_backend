"""Envelope enumerations aligned with ADR-0002."""

from __future__ import annotations

from enum import StrEnum


class MessageKind(StrEnum):
    """Physical envelope message taxonomy (ADR-0002)."""

    DOMAIN = "domain"
    INTEGRATION = "integration"
    COMMAND = "command"
    REPLY = "reply"
    PROJECTION = "projection"


class DataClassification(StrEnum):
    """Sensitivity classification driving log redaction and stream ACL."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class AggregateScope(StrEnum):
    """Whether the message is bound to a domain aggregate."""

    AGGREGATE = "aggregate"
    NON_AGGREGATE = "non_aggregate"


# Message kinds that require aggregate_version when aggregate-scoped and ordering applies.
ORDERING_MESSAGE_KINDS: frozenset[MessageKind] = frozenset(
    {
        MessageKind.DOMAIN,
        MessageKind.INTEGRATION,
        MessageKind.COMMAND,
    }
)
