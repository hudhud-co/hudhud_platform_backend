"""Outbox relay worker tests — fake broker and in-memory store."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fakes.nats_fake import FakeJetStreamClient
from test_nats_publisher import _sample_envelope_dict

from legacy_event_bridge.application.publisher import OutboxPublisher
from legacy_event_bridge.application.readiness import evaluate_readiness
from legacy_event_bridge.application.relay import OutboxRelayWorker, RelayWorkerSettings
from legacy_event_bridge.config import RuntimeEnvironment, load_settings
from legacy_event_bridge.domain.types import OutboxRecord
from legacy_event_bridge.infrastructure.memory import MemoryBridgeStore
from legacy_event_bridge.infrastructure.nats.publisher import JetStreamPublisherAdapter
from legacy_event_bridge.infrastructure.nats.subjects import A1_SUBJECT
from legacy_event_bridge.ports import OutboxStorePort, TransactionPort


@dataclass
class TransactionTrackingStore:
    """Wraps a store and records whether a transaction is open during publish."""

    inner: MemoryBridgeStore
    transaction_open: bool = False
    publish_while_transaction_open: bool = False

    def begin(self) -> TransactionPort:
        self.transaction_open = True
        return self.inner.begin()

    def recover_stale_processing(self, *, now: datetime) -> int:
        return self.inner.recover_stale_processing(now=now)

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]:
        claimed = self.inner.claim_batch(
            owner=owner,
            batch_size=batch_size,
            lease_until=lease_until,
            now=now,
        )
        self.transaction_open = False
        return claimed

    def apply_publish_decision(self, **kwargs: object) -> None:
        return self.inner.apply_publish_decision(**kwargs)  # type: ignore[arg-type]

    def insert(self, *args: object, **kwargs: object) -> OutboxRecord:
        return self.inner.insert(*args, **kwargs)  # type: ignore[arg-type]

    def get_by_event_id(self, event_id: object) -> OutboxRecord | None:
        return self.inner.get_by_event_id(event_id)  # type: ignore[arg-type]

    def mark_publish_inflight(self) -> None:
        if self.transaction_open:
            self.publish_while_transaction_open = True


def _relay_worker(
    store: OutboxStorePort,
    fake: FakeJetStreamClient,
) -> OutboxRelayWorker:
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=adapter,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
    )
    return OutboxRelayWorker(
        publisher=publisher,
        nats_adapter=adapter,
        settings=RelayWorkerSettings(
            batch_size=10,
            poll_interval_seconds=0.01,
            lease_seconds=30,
            owner_id="relay",
        ),
    )


def test_transient_retry_then_publish(store: MemoryBridgeStore) -> None:
    fake = FakeJetStreamClient(should_fail_transient=True)
    worker = _relay_worker(store, fake)
    row = store.insert(
        store.begin(),
        event_id=uuid4(),
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        landing_id=uuid4(),
        max_attempts=5,
        at=datetime.now(tz=UTC),
    )
    store.begin().commit()
    outcome = worker.run_once()
    assert outcome.retry_count == 1
    updated = store.outbox[row.id]
    assert updated.status == "pending"
    store.outbox[row.id] = replace(updated, next_attempt_at=datetime.now(tz=UTC))
    fake.should_fail_transient = False
    outcome = worker.run_once()
    assert outcome.published_count == 1


def test_configurable_retry_backoff_seconds(store: MemoryBridgeStore) -> None:
    fake = FakeJetStreamClient(should_fail_transient=True)
    adapter = JetStreamPublisherAdapter(fake, publish_timeout_seconds=1.0)
    backoff_seconds = [7, 14]
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=adapter,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
        retry_backoff_seconds=backoff_seconds,
    )
    now = datetime.now(tz=UTC)
    row = store.insert(
        store.begin(),
        event_id=uuid4(),
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        landing_id=uuid4(),
        max_attempts=5,
        at=now,
    )
    store.begin().commit()
    scheduled_at = datetime.now(tz=UTC)
    publisher.publish_pending()
    updated = store.outbox[row.id]
    expected_delay = backoff_seconds[
        min(updated.attempt_count, len(backoff_seconds) - 1)
    ]
    delta = (updated.next_attempt_at - scheduled_at).total_seconds()
    assert expected_delay <= delta <= expected_delay + 1


def test_permanent_quarantine_on_forbidden_subject(store: MemoryBridgeStore) -> None:
    fake = FakeJetStreamClient()
    worker = _relay_worker(store, fake)
    row = store.insert(
        store.begin(),
        event_id=uuid4(),
        subject="hudhud.wallet.forbidden.event.v1",
        payload_json=_sample_envelope_dict(),
        landing_id=uuid4(),
        max_attempts=5,
        at=datetime.now(tz=UTC),
    )
    store.begin().commit()
    outcome = worker.run_once()
    assert outcome.quarantined_count == 1
    assert store.outbox[row.id].status == "quarantined"


def test_max_attempts_quarantine(store: MemoryBridgeStore) -> None:
    fake = FakeJetStreamClient(should_timeout=True)
    worker = _relay_worker(store, fake)
    row = store.insert(
        store.begin(),
        event_id=uuid4(),
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        landing_id=uuid4(),
        max_attempts=1,
        at=datetime.now(tz=UTC),
    )
    store.begin().commit()
    worker.run_once()
    assert store.outbox[row.id].status == "quarantined"


def test_stale_lease_recovery(store: MemoryBridgeStore) -> None:
    fake = FakeJetStreamClient()
    worker = _relay_worker(store, fake)
    row = store.insert(
        store.begin(),
        event_id=uuid4(),
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        landing_id=uuid4(),
        max_attempts=5,
        at=datetime.now(tz=UTC),
    )
    store.begin().commit()
    stale = replace(
        row,
        status="processing",
        attempt_count=1,
        processing_owner="stale-owner",
        processing_until=datetime.now(tz=UTC) - timedelta(seconds=60),
    )
    store.outbox[row.id] = stale
    outcome = worker.run_once()
    assert outcome.published_count == 1
    assert store.outbox[row.id].status == "published"


def test_no_transaction_held_during_broker_wait() -> None:
    inner = MemoryBridgeStore()
    tracking = TransactionTrackingStore(inner=inner)
    fake = FakeJetStreamClient()

    class TrackingAdapter(JetStreamPublisherAdapter):
        def publish(self, **kwargs: object) -> object:
            tracking.mark_publish_inflight()
            return super().publish(**kwargs)  # type: ignore[arg-type]

    adapter = TrackingAdapter(fake, publish_timeout_seconds=1.0)
    publisher = OutboxPublisher(
        outbox_store=tracking,
        publisher=adapter,
        owner_id="relay",
        batch_size=10,
        lease_seconds=30,
    )
    tracking.insert(
        tracking.begin(),
        event_id=uuid4(),
        subject=A1_SUBJECT,
        payload_json=_sample_envelope_dict(),
        landing_id=uuid4(),
        max_attempts=5,
        at=datetime.now(tz=UTC),
    )
    tracking.begin().commit()
    publisher.publish_pending()
    assert not tracking.publish_while_transaction_open


def test_graceful_worker_stop(store: MemoryBridgeStore) -> None:
    fake = FakeJetStreamClient()
    worker = _relay_worker(store, fake)
    worker.request_stop()
    worker.run_until_stopped()
    assert not worker.is_running
    assert fake.drained
    assert fake.closed


def test_readiness_when_relay_enabled_and_nats_ok() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_dev_no_auth=True,
    )
    report = evaluate_readiness(
        settings=settings,
        engine=None,
        persistence_wired=True,
        nats_reachable=True,
    )
    assert report.ready
    assert report.checks["nats_publisher_active"]
    assert report.checks["cdc_adapter_deferred"]


def test_readiness_blocks_invalid_relay_config() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        relay_enabled=True,
        nats_url=None,
    )
    report = evaluate_readiness(
        settings=settings,
        engine=None,
        persistence_wired=True,
        nats_reachable=False,
    )
    assert not report.ready
    assert "relay_configuration_invalid" in report.blockers
