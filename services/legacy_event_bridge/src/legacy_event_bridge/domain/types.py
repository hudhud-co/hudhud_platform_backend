"""Domain value types for the Bridge durable pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class MappingState(StrEnum):
    PENDING = "pending"
    MAPPED = "mapped"
    QUARANTINED = "quarantined"


class SourceTable(StrEnum):
    SHIPMENT_EVENTS = "shipment_events"
    AUDIT_LOGS = "audit_logs"


# Registry-backed allowlist — use allowlisted_source_tables() at runtime.
ALLOWLISTED_SOURCE_TABLES: frozenset[str] = frozenset(
    {SourceTable.SHIPMENT_EVENTS, SourceTable.AUDIT_LOGS}
)


@dataclass(frozen=True, slots=True)
class SourceRowIdentity:
    source_system: str
    source_table: str
    source_pk: UUID

    def dedupe_key(self) -> tuple[str, str, str]:
        return (self.source_system, self.source_table, str(self.source_pk))


@dataclass(frozen=True, slots=True)
class CdcChange:
    """Normalized CDC boundary input — allowlisted fields only."""

    source_system: str
    source_table: str
    source_pk: UUID
    source_position: str
    capture_slot: str
    normalized_fields: dict[str, Any]
    received_at: datetime


@dataclass(frozen=True, slots=True)
class LandingRecord:
    id: UUID
    identity: SourceRowIdentity
    source_position: str
    mapper_version: str
    normalized_fields: dict[str, Any]
    received_at: datetime
    mapping_state: MappingState
    mapping_attempt_count: int = 0
    mapped_at: datetime | None = None
    quarantined_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    capture_source: str
    last_durably_landed_position: str | None
    last_feedback_eligible_position: str | None
    last_external_slot_advanced_position: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: UUID
    event_id: UUID
    subject: str
    payload_json: dict[str, Any]
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    processing_owner: str | None
    processing_until: datetime | None
    published_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    landing_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ObservationContract:
    event_type: str
    event_version: int
    subject: str
    namespace: UUID
    schema_uri: str


def _load_registry_contracts() -> tuple[dict[str, ObservationContract], frozenset[str]]:
    from legacy_event_bridge.infrastructure.contracts.registry import (  # noqa: PLC0415
        load_bridge_registry,
    )

    loaded = load_bridge_registry()
    return loaded.contracts_by_table, loaded.allowlisted_source_tables


def contract_by_table() -> dict[str, ObservationContract]:
    contracts, _ = _load_registry_contracts()
    return contracts


def allowlisted_source_tables() -> frozenset[str]:
    _, allowlisted = _load_registry_contracts()
    return allowlisted


@dataclass
class PipelineBatchResult:
    landed_count: int = 0
    duplicate_count: int = 0
    mapped_count: int = 0
    mapping_failures: int = 0
    published_count: int = 0
    feedback_positions: list[str] = field(default_factory=list)
