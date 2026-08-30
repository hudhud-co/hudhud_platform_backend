"""Core integration message envelope model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from event_envelope.compatibility import SUPPORTED_ENVELOPE_VERSION
from event_envelope.enums import (
    ORDERING_MESSAGE_KINDS,
    AggregateScope,
    DataClassification,
    MessageKind,
)
from event_envelope.errors import EnvelopeValidationError
from event_envelope.media_refs import EnvelopeMetadata, MediaRef
from event_envelope.primitives import format_utc_datetime, format_uuid, parse_utc_datetime
from event_envelope.trace import validate_traceparent

_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "envelope_version",
        "event_id",
        "event_type",
        "event_version",
        "occurred_at",
        "published_at",
        "producer",
        "message_kind",
        "aggregate_scope",
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "correlation_id",
        "causation_id",
        "traceparent",
        "tracestate",
        "tenant_id",
        "organization_id",
        "data_classification",
        "pii_present",
        "schema_uri",
        "payload",
        "metadata",
        "media_refs",
        "unknown_fields",
    }
)


class EventEnvelope(BaseModel):
    """ADR-0002 integration message envelope (technical contract only)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    envelope_version: int = Field(default=SUPPORTED_ENVELOPE_VERSION, ge=1)
    event_id: UUID
    event_type: str = Field(..., min_length=1, max_length=128)
    event_version: int = Field(..., ge=1)
    occurred_at: datetime
    published_at: datetime | None = None
    producer: str = Field(..., min_length=1, max_length=64)
    message_kind: MessageKind
    aggregate_scope: AggregateScope
    aggregate_type: str | None = Field(default=None, max_length=64)
    aggregate_id: UUID | None = None
    aggregate_version: int | None = Field(default=None, ge=0)
    correlation_id: UUID
    causation_id: UUID | None = None
    traceparent: str | None = None
    tracestate: str | None = None
    tenant_id: UUID | None = None
    organization_id: UUID | None = None
    data_classification: DataClassification
    pii_present: bool
    schema_uri: str | None = None
    payload: dict[str, Any]
    metadata: EnvelopeMetadata | dict[str, Any] | None = None
    media_refs: list[MediaRef] | None = None
    unknown_fields: dict[str, Any] = Field(default_factory=dict, exclude=True)

    _KNOWN_FIELDS: ClassVar[frozenset[str]] = _KNOWN_FIELDS

    @classmethod
    def known_field_names(cls) -> frozenset[str]:
        return cls._KNOWN_FIELDS

    @field_validator("occurred_at", "published_at", mode="before")
    @classmethod
    def _parse_datetime(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise EnvelopeValidationError("occurred_at", "timestamp must be timezone-aware UTC")
            return value.astimezone(UTC)
        if isinstance(value, str):
            try:
                return parse_utc_datetime(value)
            except ValueError as exc:
                raise EnvelopeValidationError("occurred_at", str(exc)) from exc
        msg = "invalid datetime value"
        raise EnvelopeValidationError("occurred_at", msg)

    @field_validator(
        "event_id",
        "correlation_id",
        "causation_id",
        "aggregate_id",
        "tenant_id",
        "organization_id",
        mode="before",
    )
    @classmethod
    def _parse_uuid(cls, value: UUID | str | None) -> UUID | None:
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        return UUID(str(value))

    @field_validator("traceparent")
    @classmethod
    def _validate_traceparent(cls, value: str | None) -> str | None:
        return validate_traceparent(value)

    @model_validator(mode="after")
    def _validate_aggregate_identity(self) -> EventEnvelope:
        if self.aggregate_scope == AggregateScope.AGGREGATE:
            if not self.aggregate_type:
                raise EnvelopeValidationError(
                    "aggregate_type",
                    "required when aggregate_scope is aggregate",
                )
            if self.aggregate_id is None:
                raise EnvelopeValidationError(
                    "aggregate_id",
                    "required when aggregate_scope is aggregate",
                )
            if (
                self.message_kind in ORDERING_MESSAGE_KINDS
                and self.aggregate_version is None
            ):
                raise EnvelopeValidationError(
                    "aggregate_version",
                    "required for ordering or optimistic concurrency on aggregate messages",
                )
        elif self.aggregate_scope == AggregateScope.NON_AGGREGATE:
            if self.aggregate_type is not None:
                raise EnvelopeValidationError(
                    "aggregate_type",
                    "must be absent when aggregate_scope is non_aggregate",
                )
            if self.aggregate_id is not None:
                raise EnvelopeValidationError(
                    "aggregate_id",
                    "must be absent when aggregate_scope is non_aggregate",
                )
            if self.aggregate_version is not None:
                raise EnvelopeValidationError(
                    "aggregate_version",
                    "must be absent when aggregate_scope is non_aggregate",
                )
        return self

    def model_dump_json_ready(self) -> dict[str, Any]:
        """Dump with deterministic UUID and timestamp formatting."""
        raw = self.model_dump(mode="python", exclude_none=True, exclude={"unknown_fields"})
        raw["envelope_version"] = self.envelope_version
        raw["event_id"] = format_uuid(self.event_id)
        raw["correlation_id"] = format_uuid(self.correlation_id)
        if self.causation_id is not None:
            raw["causation_id"] = format_uuid(self.causation_id)
        if self.aggregate_id is not None:
            raw["aggregate_id"] = format_uuid(self.aggregate_id)
        if self.tenant_id is not None:
            raw["tenant_id"] = format_uuid(self.tenant_id)
        if self.organization_id is not None:
            raw["organization_id"] = format_uuid(self.organization_id)
        raw["occurred_at"] = format_utc_datetime(self.occurred_at)
        if self.published_at is not None:
            raw["published_at"] = format_utc_datetime(self.published_at)
        raw["message_kind"] = self.message_kind.value
        raw["aggregate_scope"] = self.aggregate_scope.value
        raw["data_classification"] = self.data_classification.value
        if self.metadata is not None and isinstance(self.metadata, EnvelopeMetadata):
            raw["metadata"] = self.metadata.model_dump(mode="json", exclude_none=True)
        if self.media_refs is not None:
            raw["media_refs"] = [
                ref.model_dump(mode="json", exclude_none=True) for ref in self.media_refs
            ]
        if self.unknown_fields:
            raw.update(self.unknown_fields)
        return raw

    def __repr__(self) -> str:
        return (
            f"EventEnvelope("
            f"event_id={format_uuid(self.event_id)!r}, "
            f"event_type={self.event_type!r}, "
            f"event_version={self.event_version!r}, "
            f"message_kind={self.message_kind.value!r}, "
            f"aggregate_scope={self.aggregate_scope.value!r}, "
            f"payload=<redacted>, "
            f"metadata=<redacted>)"
        )

    def safe_log_fields(self) -> dict[str, Any]:
        """Structured log fields without sensitive payload or metadata values."""
        fields: dict[str, Any] = {
            "envelope_version": self.envelope_version,
            "event_id": format_uuid(self.event_id),
            "event_type": self.event_type,
            "event_version": self.event_version,
            "producer": self.producer,
            "message_kind": self.message_kind.value,
            "aggregate_scope": self.aggregate_scope.value,
            "correlation_id": format_uuid(self.correlation_id),
            "data_classification": self.data_classification.value,
            "pii_present": self.pii_present,
        }
        if self.aggregate_id is not None:
            fields["aggregate_id"] = format_uuid(self.aggregate_id)
        if self.aggregate_type is not None:
            fields["aggregate_type"] = self.aggregate_type
        if self.aggregate_version is not None:
            fields["aggregate_version"] = self.aggregate_version
        if self.traceparent is not None:
            fields["traceparent"] = self.traceparent
        if self.causation_id is not None:
            fields["causation_id"] = format_uuid(self.causation_id)
        return fields
