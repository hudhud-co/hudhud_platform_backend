"""State and action enumerations aligned with ADR-0008."""

from __future__ import annotations

from enum import StrEnum


class InboxStatus(StrEnum):
    """Integration inbox row lifecycle states."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class OutboxStatus(StrEnum):
    """Integration outbox row lifecycle states."""

    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    QUARANTINED = "quarantined"


class JetStreamConsumerAction(StrEnum):
    """Broker acknowledgement action for a consumer delivery attempt."""

    ACK = "ack"
    NAK = "nak"
    DEFER = "defer"


class QuarantineRedeliveryPolicy(StrEnum):
    """Explicit terminal/replay policy when a quarantined inbox row is redelivered."""

    ACK_TERMINAL = "ack_terminal"
    REPLAY_RESET = "replay_reset"


class RetryClassification(StrEnum):
    """Retry outcome classification without fixed production timing."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POISON = "poison"


class TransportDedupeAuthority(StrEnum):
    """Where idempotency authority lives for at-least-once messaging."""

    BOUNDED_TRANSPORT = "bounded_transport"
    SERVICE_INBOX = "service_inbox"
    DOMAIN_EFFECT = "domain_effect"
