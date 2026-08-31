"""Pipeline orchestration across CDC, landing, mapping, and publish."""

from __future__ import annotations

from legacy_event_bridge.application.landing import (
    LandingCoordinator,
    ReplicationFeedbackCoordinator,
)
from legacy_event_bridge.application.mapping import MappingService
from legacy_event_bridge.application.publisher import OutboxPublisher
from legacy_event_bridge.domain.types import PipelineBatchResult
from legacy_event_bridge.ports import CdcAdapterPort


class BridgePipeline:
    """End-to-end durable observation pipeline for one processing tick."""

    def __init__(
        self,
        *,
        cdc_adapter: CdcAdapterPort,
        landing_coordinator: LandingCoordinator,
        feedback_coordinator: ReplicationFeedbackCoordinator,
        mapping_service: MappingService,
        outbox_publisher: OutboxPublisher,
        capture_source: str,
        poll_limit: int = 100,
    ) -> None:
        self._cdc = cdc_adapter
        self._landing = landing_coordinator
        self._feedback = feedback_coordinator
        self._mapping = mapping_service
        self._publisher = outbox_publisher
        self._capture_source = capture_source
        self._poll_limit = poll_limit

    def run_once(self) -> PipelineBatchResult:
        changes = self._cdc.poll_batch(
            capture_source=self._capture_source,
            limit=self._poll_limit,
        )
        landing_outcome = self._landing.land_batch(changes)
        feedback_positions = self._feedback.send_post_commit_feedback(
            capture_source=self._capture_source,
            positions=landing_outcome.durable_positions,
        )
        mapping_outcome = self._mapping.map_pending(limit=self._poll_limit)
        publish_outcome = self._publisher.publish_pending()
        return PipelineBatchResult(
            landed_count=landing_outcome.landed_count,
            duplicate_count=landing_outcome.duplicate_count,
            mapped_count=mapping_outcome.mapped_count,
            mapping_failures=mapping_outcome.failure_count,
            published_count=publish_outcome.published_count,
            feedback_positions=feedback_positions,
        )
