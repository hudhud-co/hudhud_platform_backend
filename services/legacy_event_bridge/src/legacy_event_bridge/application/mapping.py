"""Mapping service — outbox insert and landing state in one transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from legacy_event_bridge.application.mapper import map_landing_to_outbox_payload
from legacy_event_bridge.domain.errors import MappingError
from legacy_event_bridge.domain.sanitize import sanitize_error_message
from legacy_event_bridge.domain.types import OutboxRecord
from legacy_event_bridge.ports import LandingStorePort, OutboxStorePort, UnitOfWorkPort


@dataclass(frozen=True, slots=True)
class MappingOutcome:
    mapped_count: int
    failure_count: int
    outbox_rows: tuple[OutboxRecord, ...]


class MappingService:
    """Maps pending landing rows to outbox envelopes atomically."""

    def __init__(
        self,
        *,
        landing_store: LandingStorePort,
        outbox_store: OutboxStorePort,
        unit_of_work: UnitOfWorkPort,
        mapper_version: str,
        source_system: str,
        max_attempts: int,
    ) -> None:
        self._landing = landing_store
        self._outbox = outbox_store
        self._uow = unit_of_work
        self._mapper_version = mapper_version
        self._source_system = source_system
        self._max_attempts = max_attempts

    def map_pending(self, *, limit: int = 100) -> MappingOutcome:
        pending = self._landing.list_pending_mapping(limit=limit)
        mapped = 0
        failures = 0
        outbox_rows: list[OutboxRecord] = []
        now = datetime.now(tz=UTC)

        for landing in pending:
            tx = self._uow.begin()
            try:
                payload_json, subject, event_id = map_landing_to_outbox_payload(
                    landing,
                    mapper_version=self._mapper_version,
                    source_system=self._source_system,
                )
                row = self._outbox.insert(
                    tx,
                    event_id=event_id,
                    subject=subject,
                    payload_json=payload_json,
                    landing_id=landing.id,
                    max_attempts=self._max_attempts,
                    at=now,
                )
                self._landing.mark_mapped(tx, landing_id=landing.id, mapped_at=now)
                tx.commit()
                mapped += 1
                outbox_rows.append(row)
            except MappingError as exc:
                tx.rollback()
                fail_tx = self._uow.begin()
                self._landing.mark_mapping_failed(
                    fail_tx,
                    landing_id=landing.id,
                    error_code="MAPPING_FAILED",
                    error_message=sanitize_error_message(str(exc)),
                    quarantine=False,
                    at=now,
                )
                fail_tx.commit()
                failures += 1
            except Exception as exc:
                tx.rollback()
                fail_tx = self._uow.begin()
                self._landing.mark_mapping_failed(
                    fail_tx,
                    landing_id=landing.id,
                    error_code="MAPPING_FAILED",
                    error_message=sanitize_error_message(str(exc)),
                    quarantine=False,
                    at=now,
                )
                fail_tx.commit()
                failures += 1

        return MappingOutcome(
            mapped_count=mapped,
            failure_count=failures,
            outbox_rows=tuple(outbox_rows),
        )
