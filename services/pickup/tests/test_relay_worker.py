"""Outbox relay worker tests — fake broker and in-memory store."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fakes.nats_fake import FakeJetStreamClient
from test_nats_publisher import _insert_pending, _sample_envelope_dict

from pickup.application.publisher import OutboxPublisher
from pickup.application.readiness import evaluate_readiness
from pickup.application.relay import OutboxRelayWorker, RelayWorkerSettings
from pickup.config import RuntimeEnvironment, load_settings
from pickup.domain.entities import OutboxRecord
from pickup.domain.value_objects import OutboxStatus
from pickup.infrastructure.memory import InMemoryPickupUnitOfWork
from pickup.infrastructure.nats.publisher import JetStreamPublisherAdapter
from pickup.ports.repository import OutboxRelayStorePort


@dataclass
class TransactionTrackingStore:
    """Wraps a store and records whether a transaction is open during publish."""

    inner: InMemoryPickupUnitOfWork
    transaction_open: bool = False
    publish_while_transaction_open: bool = False

    def recover_stale_processing(self, *, now: datetime) -> int:
        self.transaction_open = True
        recovered = self.inner.recover_stale_processing(now=now)
        self.transaction_open = False
        return recovered

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]:
        self.transaction_open = True
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

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        return self.inner.outbox.get_by_event_id(event_id)

    def mark_publish_inflight(self) -> None:
        if self.transaction_open:
            self.publish_while_transaction_open = True


def _relay_worker(
    store: OutboxRelayStorePort,
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


def test_transient_retry_then_publish() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient(should_fail_transient=True)
    worker = _relay_worker(store, fake)
    row = _insert_pending(store)
    outcome = worker.run_once()
    assert outcome.retry_count == 1
    updated = store.outbox.get_by_event_id(row.event_id)
    assert updated is not None
    assert updated.status is OutboxStatus.PENDING
    store._outbox[row.id] = replace(updated, next_attempt_at=datetime.now(tz=UTC))
    fake.should_fail_transient = False
    outcome = worker.run_once()
    assert outcome.published_count == 1


def test_configurable_retry_backoff_seconds() -> None:
    store = InMemoryPickupUnitOfWork()
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
    row = _insert_pending(store)
    scheduled_at = datetime.now(tz=UTC)
    publisher.publish_pending()
    updated = store.outbox.get_by_event_id(row.event_id)
    assert updated is not None
    expected_delay = backoff_seconds[min(updated.attempt_count, len(backoff_seconds) - 1)]
    delta = (updated.next_attempt_at - scheduled_at).total_seconds()
    assert expected_delay <= delta <= expected_delay + 1


def test_permanent_quarantine_on_forbidden_subject() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient()
    worker = _relay_worker(store, fake)
    row = _insert_pending(store, subject="hudhud.wallet.forbidden.event.v1")
    outcome = worker.run_once()
    assert outcome.quarantined_count == 1
    updated = store.outbox.get_by_event_id(row.event_id)
    assert updated is not None
    assert updated.status is OutboxStatus.QUARANTINED


def test_max_attempts_quarantine() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient(should_timeout=True)
    worker = _relay_worker(store, fake)
    row = _insert_pending(store, max_attempts=1)
    worker.run_once()
    updated = store.outbox.get_by_event_id(row.event_id)
    assert updated is not None
    assert updated.status is OutboxStatus.QUARANTINED


def test_stale_lease_recovery() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient()
    worker = _relay_worker(store, fake)
    row = _insert_pending(store)
    store._outbox[row.id] = replace(
        row,
        status=OutboxStatus.PROCESSING,
        attempt_count=1,
        processing_owner="stale-owner",
        processing_until=datetime.now(tz=UTC) - timedelta(seconds=60),
    )
    outcome = worker.run_once()
    assert outcome.published_count == 1
    updated = store.outbox.get_by_event_id(row.event_id)
    assert updated is not None
    assert updated.status is OutboxStatus.PUBLISHED


def test_no_transaction_held_during_broker_wait() -> None:
    inner = InMemoryPickupUnitOfWork()
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
    _insert_pending(inner)
    publisher.publish_pending()
    assert not tracking.publish_while_transaction_open


def test_graceful_worker_stop() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient()
    worker = _relay_worker(store, fake)
    worker.request_stop()
    worker.run_until_stopped()
    assert not worker.is_running
    assert fake.drained
    assert fake.closed


def test_published_only_after_valid_puback() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient(should_timeout=True)
    worker = _relay_worker(store, fake)
    row = _insert_pending(store, max_attempts=5)
    worker.run_once()
    updated = store.outbox.get_by_event_id(row.event_id)
    assert updated is not None
    assert updated.status is not OutboxStatus.PUBLISHED
    assert updated.published_at is None


def test_stable_event_id_survives_retry() -> None:
    store = InMemoryPickupUnitOfWork()
    fake = FakeJetStreamClient(should_fail_transient=True)
    worker = _relay_worker(store, fake)
    event_id = uuid4()
    row = _insert_pending(
        store,
        event_id=event_id,
        payload=_sample_envelope_dict(event_id=event_id),
    )
    worker.run_once()
    fake.should_fail_transient = False
    updated = store.outbox.get_by_event_id(event_id)
    assert updated is not None
    store._outbox[row.id] = replace(updated, next_attempt_at=datetime.now(tz=UTC))
    worker.run_once()
    assert fake.publish_log[0][2] == str(event_id)


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
        authorization_configured=True,
        shipment_eligibility_configured=True,
        nats_reachable=True,
    )
    assert report.ready
    assert report.checks["nats_publisher_active"]
    assert report.checks["production_ready_false"]


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
        authorization_configured=True,
        shipment_eligibility_configured=True,
        nats_reachable=False,
    )
    assert not report.ready
    assert "relay_configuration_invalid" in report.blockers


def test_readiness_blocks_nats_unreachable_when_relay_enabled() -> None:
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
        authorization_configured=True,
        shipment_eligibility_configured=True,
        nats_reachable=False,
    )
    assert not report.ready
    assert "nats_unreachable" in report.blockers


def test_production_ready_remains_false() -> None:
    settings = load_settings(environment=RuntimeEnvironment.LOCAL)
    assert settings.production_ready is False
    report = evaluate_readiness(
        settings=settings,
        engine=None,
        persistence_wired=True,
        authorization_configured=True,
        shipment_eligibility_configured=True,
    )
    assert report.checks["production_ready_false"]
    assert "nats_publisher_not_enabled" in report.blockers
