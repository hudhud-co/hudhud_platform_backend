"""Media reference primitives for out-of-band blob storage."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MediaRef(BaseModel):
    """Pointer to evidence media stored outside the JetStream message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref_type: str = Field(..., min_length=1, max_length=32)
    bucket: str = Field(..., min_length=1, max_length=256)
    key: str = Field(..., min_length=1, max_length=1024)
    content_type: str | None = Field(default=None, max_length=128)

    def __repr__(self) -> str:
        return f"MediaRef(ref_type={self.ref_type!r}, bucket=<redacted>, key=<redacted>)"


class EnvelopeMetadata(BaseModel):
    """Non-domain envelope metadata (ADR-0002 optional keys)."""

    model_config = ConfigDict(extra="allow")

    source_ip: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    locale: str | None = None
    replay: bool | None = None
    replay_source: str | None = None
    idempotency_key: str | None = None

    def __repr__(self) -> str:
        return "EnvelopeMetadata(<redacted>)"

    def safe_dict(self) -> dict[str, Any]:
        """Return keys only — values withheld for logging."""
        return {key: "<redacted>" for key in self.model_dump(exclude_none=True)}
