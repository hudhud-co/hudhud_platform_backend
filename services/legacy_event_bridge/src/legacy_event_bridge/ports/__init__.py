"""Port protocols for the Bridge pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from legacy_event_bridge.domain.publish import PublishResult
from legacy_event_bridge.domain.types import (
    CdcChange,
    CheckpointRecord,
    LandingRecord,
    OutboxRecord,
)


class TransactionPort(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UnitOfWorkPort(Protocol):
    def begin(self) -> TransactionPort: ...


class CdcAdapterPort(Protocol):
    """CDC adapter boundary — fixture-fed in W5-A."""

    def poll_batch(self, *, capture_source: str, limit: int) -> list[CdcChange]: ...


class LandingStorePort(Protocol):
    def insert_landing(
        self,
        tx: TransactionPort,
        *,
        change: CdcChange,
        mapper_version: str,
    ) -> tuple[LandingRecord | None, bool]:
        """Insert landing row. Returns (record, created). None + False when duplicate."""

    def get_by_identity(
        self,
        *,
        source_system: str,
        source_table: str,
        source_pk: UUID,
    ) -> LandingRecord | None: ...

    def get_by_id(self, *, landing_id: UUID) -> LandingRecord | None: ...

    def list_pending_mapping(self, *, limit: int) -> list[LandingRecord]: ...

    def mark_mapped(
        self,
        tx: TransactionPort,
        *,
        landing_id: UUID,
        mapped_at: datetime,
    ) -> None: ...

    def mark_mapping_failed(
        self,
        tx: TransactionPort,
        *,
        landing_id: UUID,
        error_code: str,
        error_message: str,
        quarantine: bool,
        attempt_count: int,
        at: datetime,
    ) -> None: ...


class CheckpointStorePort(Protocol):
    def get(self, *, capture_source: str) -> CheckpointRecord | None: ...

    def update_durable_landed(
        self,
        tx: TransactionPort,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None: ...

    def mark_feedback_eligible(
        self,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None: ...

    def mark_external_slot_advanced(
        self,
        *,
        capture_source: str,
        position: str,
        at: datetime,
    ) -> None: ...


class OutboxStorePort(Protocol):
    def insert(
        self,
        tx: TransactionPort,
        *,
        event_id: UUID,
        subject: str,
        payload_json: dict,
        landing_id: UUID,
        max_attempts: int,
        at: datetime,
    ) -> OutboxRecord: ...

    def recover_stale_processing(self, *, now: datetime) -> int: ...

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]: ...

    def apply_publish_decision(
        self,
        *,
        outbox_id: UUID,
        status: str,
        clear_owner: bool,
        clear_lease: bool,
        published_at: datetime | None,
        next_attempt_at: datetime | None,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> None: ...

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None: ...


class PublisherPort(Protocol):
    """JetStream publisher boundary."""

    def publish(
        self,
        *,
        subject: str,
        payload_json: dict,
        transport_msg_id: str,
    ) -> PublishResult:
        """Return publish outcome with broker ACK status."""


class ReplicationFeedbackPort(Protocol):
    """External replication slot feedback — only after durable landing commit."""

    def send_feedback(
        self,
        *,
        capture_source: str,
        position: str,
    ) -> bool:
        """Return True when external slot feedback succeeded."""
