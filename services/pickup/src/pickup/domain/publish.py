"""Publish attempt result types — no payload in error surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Outcome of a single JetStream publish attempt."""

    ack_received: bool
    error_code: str | None = None
    error_message: str | None = None
