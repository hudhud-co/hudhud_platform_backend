"""Pipeline transaction boundary and durability tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from conftest import (
    CAPTURE_SOURCE,
    MAPPER_VERSION,
    SOURCE_SYSTEM,
    audit_change,
    shipment_change,
)
from messaging_conformance import (
    OutboxRecordSnapshot,
    OutboxStatus,
    RetryClassification,
    decide_outbox_publish_result,
)
from messaging_conformance.retry import classify_retry_error, should_quarantine

from legacy_event_bridge.application.landing import (
    LandingCoordinator,
    ReplicationFeedbackCoordinator,
)
from legacy_event_bridge.application.mapping import MappingService
from legacy_event_bridge.application.publisher import OutboxPublisher
from legacy_event_bridge.domain.sanitize import sanitize_error_message
from legacy_event_bridge.domain.types import MappingState
from legacy_event_bridge.infrastructure.memory import FixtureCdcAdapter, MemoryBridgeStore


def test_source_allowlist_rejects_unknown_table(store: MemoryBridgeStore) -> None:
    coordinator = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    bad_change = shipment_change()
    bad_change = bad_change.__class__(
        source_system=bad_change.source_system,
        source_table="wallet_ledger_entries",
        source_pk=bad_change.source_pk,
        source_position=bad_change.source_position,
        capture_slot=bad_change.capture_slot,
        normalized_fields=bad_change.normalized_fields,
        received_at=bad_change.received_at,
    )
    outcome = coordinator.land_batch([bad_change])
    assert outcome.landed_count == 0
    assert outcome.rejected_count == 1


def test_landing_deduplication_on_replay(store: MemoryBridgeStore) -> None:
    coordinator = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    change = shipment_change()
    first = coordinator.land_batch([change])
    second = coordinator.land_batch([change])
    assert first.landed_count == 1
    assert second.duplicate_count == 1
    assert len(store.landings) == 1


def test_duplicate_replay_with_new_lsn_advances_checkpoint_for_feedback(
    store: MemoryBridgeStore,
) -> None:
    coordinator = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    feedback = ReplicationFeedbackCoordinator(checkpoint_store=store, feedback_port=store)
    first_change = shipment_change(source_position="0/AAAAAA")
    replay_change = shipment_change(source_position="0/BBBBBB")
    coordinator.land_batch([first_change])
    outcome = coordinator.land_batch([replay_change])
    assert outcome.duplicate_count == 1
    assert outcome.durable_positions == ["0/BBBBBB"]
    acked = feedback.send_post_commit_feedback(
        capture_source=CAPTURE_SOURCE,
        positions=outcome.durable_positions,
    )
    assert acked == ["0/BBBBBB"]
    checkpoint = store.get(capture_source=CAPTURE_SOURCE)
    assert checkpoint is not None
    assert checkpoint.last_durably_landed_position == "0/BBBBBB"


def test_checkpoint_updated_atomically_with_landing(store: MemoryBridgeStore) -> None:
    coordinator = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    change = shipment_change(source_position="0/DEADBEEF")
    coordinator.land_batch([change])
    checkpoint = store.get(capture_source=CAPTURE_SOURCE)
    assert checkpoint is not None
    assert checkpoint.last_durably_landed_position == "0/DEADBEEF"
    assert checkpoint.last_external_slot_advanced_position is None


def test_feedback_only_after_commit_and_external_advance(store: MemoryBridgeStore) -> None:
    feedback = ReplicationFeedbackCoordinator(checkpoint_store=store, feedback_port=store)
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    change = shipment_change(source_position="0/FEED001")
    outcome = landing.land_batch([change])
    assert store.feedback_log == []
    acked = feedback.send_post_commit_feedback(
        capture_source=CAPTURE_SOURCE,
        positions=outcome.durable_positions,
    )
    assert acked == ["0/FEED001"]
    checkpoint = store.get(capture_source=CAPTURE_SOURCE)
    assert checkpoint is not None
    assert checkpoint.last_feedback_eligible_position == "0/FEED001"
    assert checkpoint.last_external_slot_advanced_position == "0/FEED001"


def test_local_checkpoint_does_not_imply_slot_when_feedback_fails(
    store: MemoryBridgeStore,
) -> None:
    store.feedback_should_succeed = False
    feedback = ReplicationFeedbackCoordinator(checkpoint_store=store, feedback_port=store)
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    change = shipment_change(source_position="0/NOSLOT")
    outcome = landing.land_batch([change])
    feedback.send_post_commit_feedback(
        capture_source=CAPTURE_SOURCE,
        positions=outcome.durable_positions,
    )
    checkpoint = store.get(capture_source=CAPTURE_SOURCE)
    assert checkpoint is not None
    assert checkpoint.last_durably_landed_position == "0/NOSLOT"
    assert checkpoint.last_feedback_eligible_position == "0/NOSLOT"
    assert checkpoint.last_external_slot_advanced_position is None


def test_mapping_outbox_transaction(store: MemoryBridgeStore) -> None:
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    landing.land_batch([shipment_change()])
    mapping = MappingService(
        landing_store=store,
        outbox_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
        max_attempts=5,
    )
    outcome = mapping.map_pending()
    assert outcome.mapped_count == 1
    landing_row = next(iter(store.landings.values()))
    assert landing_row.mapping_state is MappingState.MAPPED
    assert len(store.outbox) == 1


def test_mapper_failure_preserves_landing(store: MemoryBridgeStore) -> None:
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    broken = shipment_change()
    broken = broken.__class__(
        source_system=broken.source_system,
        source_table=broken.source_table,
        source_pk=broken.source_pk,
        source_position=broken.source_position,
        capture_slot=broken.capture_slot,
        normalized_fields={"source_module": "delivery_task"},
        received_at=broken.received_at,
    )
    landing.land_batch([broken])
    mapping = MappingService(
        landing_store=store,
        outbox_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
        max_attempts=5,
    )
    outcome = mapping.map_pending()
    assert outcome.failure_count == 1
    row = next(iter(store.landings.values()))
    assert row.mapping_state is MappingState.PENDING
    assert len(store.outbox) == 0


def test_publisher_ack_marks_published(store: MemoryBridgeStore) -> None:
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    landing.land_batch([audit_change()])
    MappingService(
        landing_store=store,
        outbox_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
        max_attempts=5,
    ).map_pending()
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=store,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
    )
    outcome = publisher.publish_pending()
    assert outcome.published_count == 1
    row = next(iter(store.outbox.values()))
    assert row.status == "published"
    assert row.published_at is not None


def test_lost_ack_duplicate_publication_same_event_id(store: MemoryBridgeStore) -> None:
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    landing.land_batch([shipment_change()])
    MappingService(
        landing_store=store,
        outbox_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
        max_attempts=5,
    ).map_pending()
    row = next(iter(store.outbox.values()))
    event_id = row.event_id
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=store,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
    )
    publisher.publish_pending()
    store.outbox[row.id] = replace(
        row,
        status="processing",
        attempt_count=row.attempt_count + 1,
        processing_owner="relay",
        processing_until=datetime.now(tz=UTC),
    )
    publisher.publish_pending()
    assert store.get_by_event_id(event_id) is not None
    assert len({entry[1] for entry in store.publish_log}) == 1


def test_poison_publish_quarantines_outbox(store: MemoryBridgeStore) -> None:
    row = store.insert(
        store.begin(),
        event_id=uuid4(),
        subject="hudhud.audit.legacy_bridge.observation.audit_entry.v1",
        payload_json={"event_id": str(uuid4())},
        landing_id=uuid4(),
        max_attempts=1,
        at=datetime.now(tz=UTC),
    )
    store.begin().commit()
    snapshot = OutboxRecordSnapshot(
        event_id=row.event_id,
        status=OutboxStatus.PROCESSING,
        attempt_count=1,
        max_attempts=1,
        processing_owner="relay",
        processing_until=datetime.now(tz=UTC),
    )
    decision = decide_outbox_publish_result(
        snapshot,
        broker_ack_received=False,
        classification=classify_retry_error("SCHEMA_MISMATCH"),
    )
    assert decision.target_status is OutboxStatus.QUARANTINED
    assert should_quarantine(
        classification=RetryClassification.PERMANENT,
        attempt_count=1,
        max_attempts=1,
    )


def test_publisher_failure_does_not_delete_landing(
    store: MemoryBridgeStore,
) -> None:
    landing = LandingCoordinator(
        landing_store=store,
        checkpoint_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
    )
    landing.land_batch([shipment_change()])
    MappingService(
        landing_store=store,
        outbox_store=store,
        unit_of_work=store,
        mapper_version=MAPPER_VERSION,
        source_system=SOURCE_SYSTEM,
        max_attempts=5,
    ).map_pending()
    store.publish_should_ack = False
    OutboxPublisher(
        outbox_store=store,
        publisher=store,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
    ).publish_pending()
    assert len(store.landings) == 1
    assert next(iter(store.landings.values())).mapping_state is MappingState.MAPPED


def test_end_to_end_pipeline(pipeline, cdc_adapter: FixtureCdcAdapter) -> None:
    cdc_adapter.enqueue(shipment_change())
    cdc_adapter.enqueue(audit_change())
    result = pipeline.run_once()
    assert result.landed_count == 2
    assert result.mapped_count == 2
    assert result.published_count == 2


def test_error_sanitization_strips_secrets() -> None:
    raw = "publish failed token=super-secret-value authorization=Bearer abc.def.ghi"
    cleaned = sanitize_error_message(raw)
    assert "super-secret-value" not in cleaned
    assert "abc.def.ghi" not in cleaned
