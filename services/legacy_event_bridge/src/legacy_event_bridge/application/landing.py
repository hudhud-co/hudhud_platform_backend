"""Atomic landing batch and durable checkpoint coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from legacy_event_bridge.domain.errors import SourceTableNotAllowedError
from legacy_event_bridge.domain.types import CdcChange
from legacy_event_bridge.ports import (
    CheckpointStorePort,
    LandingStorePort,
    ReplicationFeedbackPort,
    UnitOfWorkPort,
)


@dataclass(frozen=True, slots=True)
class LandingBatchOutcome:
    landed_count: int
    duplicate_count: int
    rejected_count: int
    durable_positions: list[str]


class LandingCoordinator:
    """Lands CDC batches and updates durable checkpoint in one transaction."""

    def __init__(
        self,
        *,
        landing_store: LandingStorePort,
        checkpoint_store: CheckpointStorePort,
        unit_of_work: UnitOfWorkPort,
        mapper_version: str,
    ) -> None:
        self._landing = landing_store
        self._checkpoint = checkpoint_store
        self._uow = unit_of_work
        self._mapper_version = mapper_version
        self._committed_positions: list[str] = []

    @property
    def last_committed_positions(self) -> list[str]:
        return list(self._committed_positions)

    def land_batch(self, changes: list[CdcChange]) -> LandingBatchOutcome:
        tx = self._uow.begin()
        landed = 0
        duplicates = 0
        rejected = 0
        positions: list[str] = []
        now = datetime.now(tz=UTC)

        try:
            for change in changes:
                try:
                    record, created = self._landing.insert_landing(
                        tx,
                        change=change,
                        mapper_version=self._mapper_version,
                    )
                except SourceTableNotAllowedError:
                    rejected += 1
                    continue

                if not created:
                    duplicates += 1
                    continue

                landed += 1
                positions.append(change.source_position)
                self._checkpoint.update_durable_landed(
                    tx,
                    capture_source=change.capture_slot,
                    position=change.source_position,
                    at=now,
                )

            tx.commit()
        except Exception:
            tx.rollback()
            raise

        self._committed_positions = positions
        return LandingBatchOutcome(
            landed_count=landed,
            duplicate_count=duplicates,
            rejected_count=rejected,
            durable_positions=positions,
        )


class ReplicationFeedbackCoordinator:
    """Sends replication feedback only after durable landing commits."""

    def __init__(
        self,
        *,
        checkpoint_store: CheckpointStorePort,
        feedback_port: ReplicationFeedbackPort,
    ) -> None:
        self._checkpoint = checkpoint_store
        self._feedback = feedback_port

    def send_post_commit_feedback(
        self,
        *,
        capture_source: str,
        positions: list[str],
    ) -> list[str]:
        if not positions:
            return []

        acknowledged: list[str] = []
        now = datetime.now(tz=UTC)
        for position in positions:
            self._checkpoint.mark_feedback_eligible(
                capture_source=capture_source,
                position=position,
                at=now,
            )
            if self._feedback.send_feedback(capture_source=capture_source, position=position):
                self._checkpoint.mark_external_slot_advanced(
                    capture_source=capture_source,
                    position=position,
                    at=now,
                )
                acknowledged.append(position)
        return acknowledged
