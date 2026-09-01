"""JetStream pull consumer adapter tests — fake NATS only."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from conftest import valid_a1_envelope
from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from tracking.application.coordinator import TimelineConsumerCoordinator
from tracking.application.readiness import evaluate_readiness
from tracking.config import PersistenceBackend, RuntimeEnvironment, load_settings
from tracking.domain.contract import A1_DURABLE_CONSUMER, A1_EVENT_TYPE, A1_STREAM, A1_SUBJECT
from tracking.domain.types import Delivery, InboxRow
from tracking.infrastructure.jetstream.binding import (
    ConsumerBindingMismatchError,
    expected_consumer_binding,
    verify_consumer_info,
)
from tracking.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from tracking.infrastructure.jetstream.connection import (
    NatsAuthRequiredError,
    bind_existing_pull_consumer,
    build_nats_connect_options,
    log_connection_failure,
    verify_nats_readiness,
)
from tracking.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from tracking.infrastructure.jetstream.delivery import delivery_from_message
from tracking.infrastructure.jetstream.worker import TimelinePullWorker
from tracking.infrastructure.memory import MemoryTrackingStore, RecordingTransport, SimulatedCrash


@dataclass
class FakeMsg:
    data: bytes
    subject: str
    stream_seq: int | None = 11
    nats_msg_id: str | None = None
    actions: list[str] = field(default_factory=list)
    nak_delay: timedelta | None = None

    @property
    def metadata(self) -> SimpleNamespace:
        return SimpleNamespace(sequence=SimpleNamespace(stream=self.stream_seq))

    @property
    def headers(self) -> dict[str, str] | None:
        if self.nats_msg_id is None:
            return None
        return {"Nats-Msg-Id": self.nats_msg_id}

    async def ack(self) -> None:
        self.actions.append("ack")

    async def nak(self, delay: timedelta | None = None) -> None:
        self.nak_delay = delay
        self.actions.append("nak")


@dataclass
class FakeConsumerConfig:
    filter_subject: str


@dataclass
class FakeConsumerInfo:
    stream_name: str
    name: str
    config: FakeConsumerConfig


class FakePullSubscription:
    """Finite scripted pull subscription for worker tests."""

    def __init__(
        self,
        *,
        info: FakeConsumerInfo,
        batches: list[list[FakeMsg]] | None = None,
        block_when_exhausted: bool = True,
    ) -> None:
        self._info = info
        self._batches = list(batches or [])
        self._block_when_exhausted = block_when_exhausted
        self._exhausted = asyncio.Event()
        self.fetch_count = 0

    async def fetch(self, batch: int, timeout: float) -> list[FakeMsg]:
        _ = (batch, timeout)
        self.fetch_count += 1
        if self._batches:
            return self._batches.pop(0)
        await asyncio.sleep(0)
        if self._block_when_exhausted:
            await self._exhausted.wait()
        return []

    def release_exhausted(self) -> None:
        self._exhausted.set()

    async def consumer_info(self) -> FakeConsumerInfo:
        return self._info


class FakeJetStream:
    def __init__(
        self,
        *,
        info: FakeConsumerInfo,
        subscription: FakePullSubscription,
    ) -> None:
        self._info = info
        self._subscription = subscription
        self.calls: list[tuple[str, ...]] = []

    async def consumer_info(self, stream: str, consumer: str) -> FakeConsumerInfo:
        self.calls.append(("consumer_info", stream, consumer))
        return self._info

    async def pull_subscribe_bind(self, *, durable: str, stream: str) -> FakePullSubscription:
        self.calls.append(("pull_subscribe_bind", durable, stream))
        return self._subscription

    async def pull_subscribe(self, *_args: object, **_kwargs: object) -> FakePullSubscription:
        self.calls.append(("pull_subscribe",))
        raise AssertionError("topology mutation forbidden")


    async def add_consumer(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append(("add_consumer",))
        raise AssertionError("topology mutation forbidden")


def _valid_info(**overrides: object) -> FakeConsumerInfo:
    expected = expected_consumer_binding()
    values = {
        "stream_name": expected.stream,
        "name": expected.durable_name,
        "config": FakeConsumerConfig(filter_subject=expected.filter_subject),
    }
    values.update(overrides)
    return FakeConsumerInfo(**values)  # type: ignore[arg-type]


def test_expected_binding_matches_topology() -> None:
    binding = expected_consumer_binding()
    assert binding.stream == "HUDHUD_SHIPMENT"
    assert binding.durable_name == "tracking_bridge_timeline_v1"
    assert binding.filter_subject == (
        "hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1"
    )


def test_verify_consumer_info_accepts_exact_binding() -> None:
    verify_consumer_info(_valid_info())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stream_name", "HUDHUD_AUDIT"),
        ("name", "other_consumer"),
        ("config", FakeConsumerConfig(filter_subject="hudhud.shipment.>")),
    ],
)
def test_verify_consumer_info_rejects_mismatch(field: str, value: object) -> None:
    info = _valid_info(**{field: value})
    with pytest.raises(ConsumerBindingMismatchError, match="mismatch"):
        verify_consumer_info(info)


def test_bind_existing_pull_consumer_verifies_and_binds_only() -> None:
    info = _valid_info()
    subscription = FakePullSubscription(info=info, batches=[[]])
    js = FakeJetStream(info=info, subscription=subscription)

    async def _run() -> None:
        bound, bound_info = await bind_existing_pull_consumer(
            js,
            settings=load_settings(
                environment=RuntimeEnvironment.TEST,
                nats_enabled=True,
                nats_url="nats://localhost:4222",
            ),
        )
        assert bound is subscription
        assert bound_info.name == A1_DURABLE_CONSUMER
        assert js.calls == [
            ("consumer_info", A1_STREAM, A1_DURABLE_CONSUMER),
            ("pull_subscribe_bind", A1_DURABLE_CONSUMER, A1_STREAM),
        ]

    asyncio.run(_run())


def test_delivery_from_message_extracts_metadata_without_payload_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    envelope = valid_a1_envelope()
    msg = FakeMsg(
        data=json.dumps(envelope).encode(),
        subject=A1_SUBJECT,
        stream_seq=42,
        nats_msg_id="broker-msg-1",
    )
    delivery = delivery_from_message(msg)
    assert delivery.subject == A1_SUBJECT
    assert delivery.stream == A1_STREAM
    assert delivery.consumer_name == A1_DURABLE_CONSUMER
    assert delivery.jetstream_seq == 42
    assert delivery.nats_msg_id == "broker-msg-1"
    assert delivery.transport_handle is msg
    for record in caplog.records:
        assert envelope["payload"]["legacy_event_type"] not in record.getMessage()


def test_broker_ack_nak_defer_mapping() -> None:
    async def _run() -> None:
        loop = asyncio.get_running_loop()
        broker = JetStreamBrokerAckClient(loop=loop, defer_delay_seconds=7.0)
        msg = FakeMsg(data=b"{}", subject=A1_SUBJECT)
        delivery = delivery_from_message(msg)
        await broker.apply_ack(delivery)
        await broker.apply_nak(delivery)
        await broker.apply_defer(delivery)
        assert msg.actions == ["ack", "nak", "nak"]
        assert msg.nak_delay == timedelta(seconds=7.0)

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("setup", "expected_store_action"),
    [
        ("valid", "ack"),
        ("duplicate", "ack"),
        ("retryable", "nak"),
        ("defer", "defer"),
    ],
)
def test_end_to_end_transport_mapping_via_worker(
    setup: str,
    expected_store_action: str,
    now: object,
    make_delivery: Callable[..., Delivery],
) -> None:
    store = MemoryTrackingStore()
    if setup == "retryable":
        store.fail_next_projection = True
    if setup == "duplicate":
        coordinator_seed = TimelineConsumerCoordinator(
            unit_of_work=store,
            inbox=store,
            observations=store,
            transport=RecordingTransport(store),
            consumer_name=A1_DURABLE_CONSUMER,
            handler_version="0.1.0",
            processing_owner="test-worker",
            lease_duration=timedelta(seconds=30),
            max_attempts=5,
            clock=lambda: now,  # type: ignore[misc, return-value]
        )
        coordinator_seed.handle(make_delivery())
        store.actions.clear()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        broker = JetStreamBrokerAckClient(loop=loop, defer_delay_seconds=5.0)
        deferred = DeferredJetStreamTransport()

        coordinator = TimelineConsumerCoordinator(
            unit_of_work=store,
            inbox=store,
            observations=store,
            transport=deferred,
            consumer_name=A1_DURABLE_CONSUMER,
            handler_version="0.1.0",
            processing_owner="test-worker",
            lease_duration=timedelta(seconds=30),
            max_attempts=5,
            clock=lambda: now,  # type: ignore[misc, return-value]
        )

        envelope = valid_a1_envelope()
        msg = FakeMsg(data=json.dumps(envelope).encode(), subject=A1_SUBJECT)
        if setup == "defer":
            store.seed_inbox(
                InboxRow(
                    id=uuid4(),
                    consumer_name=A1_DURABLE_CONSUMER,
                    event_id=UUID(envelope["event_id"]),
                    event_type=A1_EVENT_TYPE,
                    event_version=1,
                    status=InboxStatus.PROCESSING,
                    processing_owner="other-replica",
                    processing_lease_until=now + timedelta(seconds=20),  # type: ignore[operator]
                    handler_version="0.1.0",
                    attempt_count=1,
                    first_received_at=now,  # type: ignore[arg-type]
                    last_received_at=now,  # type: ignore[arg-type]
                    processed_at=None,
                    quarantined_at=None,
                    last_error_code=None,
                    last_error_message=None,
                    jetstream_stream=A1_STREAM,
                    jetstream_seq=1,
                    correlation_id=None,
                    nats_msg_id=None,
                )
            )

        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[[msg]],
            block_when_exhausted=False,
        )
        worker = TimelinePullWorker(
            subscription=subscription,
            coordinator=coordinator,
            broker=broker,
            deferred_transport=deferred,
            pull_batch_size=1,
            pull_fetch_timeout_seconds=0.1,
            handler_concurrency=1,
            shutdown_timeout_seconds=1.0,
        )
        processed = await asyncio.wait_for(worker.poll_once(), timeout=2.0)
        assert processed == 1
        assert deferred.pending[-1].action.value == expected_store_action
        if expected_store_action == "defer":
            assert msg.actions[-1] == "nak"
            assert msg.nak_delay == timedelta(seconds=5.0)
        else:
            assert msg.actions[-1] == expected_store_action

    asyncio.run(_run())


def test_quarantine_persistence_failure_naks(
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    now: object,
) -> None:
    store.fail_next_quarantine = True
    coordinator = TimelineConsumerCoordinator(
        unit_of_work=store,
        inbox=store,
        observations=store,
        transport=RecordingTransport(store),
        consumer_name=A1_DURABLE_CONSUMER,
        handler_version="0.1.0",
        processing_owner="test-worker",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=lambda: now,  # type: ignore[misc, return-value]
    )
    outcome = coordinator.handle(
        make_delivery(valid_a1_envelope(producer="shipment"))
    )
    assert outcome.reason == "quarantine_persistence_failure_nak"
    assert outcome.jetstream_action is JetStreamConsumerAction.NAK
    assert store.actions[-1] == "nak"


def test_malformed_delivery_fingerprints_without_projection(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
) -> None:
    delivery = Delivery(
        body=b"not-json",
        subject=A1_SUBJECT,
        stream=A1_STREAM,
        consumer_name=A1_DURABLE_CONSUMER,
        jetstream_seq=99,
    )
    outcome = coordinator.handle(delivery)
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.timeline_entry_count() == 0
    rows = [row for row in store._inbox.values() if row.consumer_name == A1_DURABLE_CONSUMER]
    assert len(rows) == 1
    synthetic = rows[0].event_id
    assert store.get_by_event_id(synthetic) is None


def test_no_synthetic_id_in_projection_for_poison(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
) -> None:
    delivery = Delivery(
        body=b"{broken",
        subject=A1_SUBJECT,
        stream=A1_STREAM,
        consumer_name=A1_DURABLE_CONSUMER,
        jetstream_seq=7,
    )
    coordinator.handle(delivery)
    assert store.timeline_entry_count() == 0


def test_crash_after_commit_before_ack(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.crash_before_ack = True
    with pytest.raises(SimulatedCrash):
        coordinator.handle(make_delivery())
    assert "commit" in store.actions
    assert "ack" not in store.actions


def test_poll_once_processes_single_batch() -> None:
    async def _run() -> None:
        store = MemoryTrackingStore()
        coordinator = TimelineConsumerCoordinator(
            unit_of_work=store,
            inbox=store,
            observations=store,
            transport=RecordingTransport(store),
            consumer_name=A1_DURABLE_CONSUMER,
            handler_version="0.1.0",
            processing_owner="test-worker",
            lease_duration=timedelta(seconds=30),
            max_attempts=5,
        )
        envelope = valid_a1_envelope()
        msg = FakeMsg(data=json.dumps(envelope).encode(), subject=A1_SUBJECT)
        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[[msg]],
            block_when_exhausted=False,
        )
        worker = TimelinePullWorker(
            subscription=subscription,
            coordinator=coordinator,
            pull_batch_size=1,
            pull_fetch_timeout_seconds=0.1,
            handler_concurrency=1,
            shutdown_timeout_seconds=1.0,
        )
        processed = await asyncio.wait_for(worker.poll_once(), timeout=2.0)
        assert processed == 1
        assert subscription.fetch_count == 1
        assert store.timeline_entry_count() == 1

    asyncio.run(_run())


def test_idle_polling_bounded_fetch_count() -> None:
    async def _run() -> None:
        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[],
            block_when_exhausted=False,
        )

        class NoopCoordinator:
            def handle(self, _delivery: Delivery) -> None:
                return None

        worker = TimelinePullWorker(
            subscription=subscription,
            coordinator=NoopCoordinator(),  # type: ignore[arg-type]
            pull_batch_size=1,
            pull_fetch_timeout_seconds=0.01,
            handler_concurrency=1,
            shutdown_timeout_seconds=1.0,
            idle_backoff_seconds=0.02,
        )
        task = asyncio.create_task(worker.run_forever())
        try:
            await asyncio.wait_for(asyncio.sleep(0.06), timeout=1.0)
        finally:
            worker.request_shutdown()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1.0)
        assert subscription.fetch_count <= 4

    asyncio.run(_run())


def test_unhandled_handler_error_naks_delivery() -> None:
    async def _run() -> None:
        envelope = valid_a1_envelope()
        msg = FakeMsg(data=json.dumps(envelope).encode(), subject=A1_SUBJECT)

        class FailingCoordinator:
            def handle(self, _delivery: Delivery) -> None:
                raise RuntimeError("simulated handler failure")

        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[[msg]],
            block_when_exhausted=False,
        )
        loop = asyncio.get_running_loop()
        broker = JetStreamBrokerAckClient(loop=loop, defer_delay_seconds=5.0)
        deferred = DeferredJetStreamTransport()
        worker = TimelinePullWorker(
            subscription=subscription,
            coordinator=FailingCoordinator(),  # type: ignore[arg-type]
            broker=broker,
            deferred_transport=deferred,
            pull_batch_size=1,
            pull_fetch_timeout_seconds=0.1,
            handler_concurrency=1,
            shutdown_timeout_seconds=1.0,
        )
        processed = await asyncio.wait_for(worker.poll_once(), timeout=2.0)
        assert processed == 1
        assert msg.actions == ["nak"]

    asyncio.run(_run())


def test_worker_backpressure_limits_concurrency() -> None:
    async def _run() -> None:
        in_flight = 0
        peak = 0

        class SlowCoordinator:
            def handle(self, _delivery: Delivery) -> None:
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                time.sleep(0.05)
                in_flight -= 1

        msgs = [FakeMsg(data=b"{}", subject=A1_SUBJECT) for _ in range(6)]
        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[msgs],
            block_when_exhausted=False,
        )
        worker = TimelinePullWorker(
            subscription=subscription,
            coordinator=SlowCoordinator(),  # type: ignore[arg-type]
            pull_batch_size=6,
            pull_fetch_timeout_seconds=0.1,
            handler_concurrency=2,
            shutdown_timeout_seconds=1.0,
        )
        processed = await asyncio.wait_for(worker.poll_once(), timeout=2.0)
        assert processed == 6
        assert peak <= 2

    asyncio.run(_run())


def test_worker_run_forever_cancellation_safe_shutdown() -> None:
    async def _run() -> None:
        store = MemoryTrackingStore()
        coordinator = TimelineConsumerCoordinator(
            unit_of_work=store,
            inbox=store,
            observations=store,
            transport=RecordingTransport(store),
            consumer_name=A1_DURABLE_CONSUMER,
            handler_version="0.1.0",
            processing_owner="test-worker",
            lease_duration=timedelta(seconds=30),
            max_attempts=5,
        )
        envelope = valid_a1_envelope()
        msg = FakeMsg(data=json.dumps(envelope).encode(), subject=A1_SUBJECT)
        info = _valid_info()
        subscription = FakePullSubscription(info=info, batches=[[msg]])
        worker = TimelinePullWorker(
            subscription=subscription,
            coordinator=coordinator,
            pull_batch_size=1,
            pull_fetch_timeout_seconds=0.05,
            handler_concurrency=1,
            shutdown_timeout_seconds=1.0,
            idle_backoff_seconds=0.01,
        )
        task = asyncio.create_task(worker.run_forever())
        try:
            await asyncio.wait_for(asyncio.sleep(0.05), timeout=1.0)
            assert store.timeline_entry_count() == 1
        finally:
            worker.request_shutdown()
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2.0)
        pending = [
            pending_task
            for pending_task in asyncio.all_tasks()
            if pending_task is not asyncio.current_task()
        ]
        assert not pending

    asyncio.run(_run())


def test_production_requires_credentials() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        nats_enabled=True,
        nats_url="nats://broker.example:4222",
        adr_0010_credentials_configured=False,
    )
    with pytest.raises(NatsAuthRequiredError):
        build_nats_connect_options(settings)


def test_production_allows_adr_gate() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        nats_enabled=True,
        nats_url="nats://broker.example:4222",
        adr_0010_credentials_configured=True,
        nats_tls_enabled=True,
    )
    options = build_nats_connect_options(settings)
    assert options["servers"] == ["nats://broker.example:4222"]
    assert isinstance(options["tls"], ssl.SSLContext)


def test_production_requires_tls() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        nats_enabled=True,
        nats_url="nats://broker.example:4222",
        adr_0010_credentials_configured=True,
        nats_tls_enabled=False,
    )
    with pytest.raises(NatsAuthRequiredError, match="TLS"):
        build_nats_connect_options(settings)


def test_local_no_auth_requires_explicit_flag() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
        allow_no_auth_local=False,
    )
    with pytest.raises(NatsAuthRequiredError):
        build_nats_connect_options(settings)


def test_readiness_blocks_memory_persistence_in_production() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        persistence_backend=PersistenceBackend.MEMORY,
        adr_0010_credentials_configured=True,
    )
    report = evaluate_readiness(
        settings=settings,
        engine=None,
        persistence_wired=True,
        nats_reachable=True,
        nats_binding_verified=True,
    )
    assert report.ready is False
    assert "memory_persistence_forbidden_in_production" in report.blockers


def test_readiness_requires_nats_binding_when_enabled() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    report = evaluate_readiness(
        settings=settings,
        engine=None,
        persistence_wired=True,
        nats_reachable=False,
        nats_binding_verified=False,
    )
    assert report.ready is False
    assert "nats_unreachable" in report.blockers
    assert "nats_binding_unverified" in report.blockers


async def _verify_readiness() -> None:
    info = _valid_info()
    subscription = FakePullSubscription(info=info)
    js = FakeJetStream(info=info, subscription=subscription)
    report = await verify_nats_readiness(
        js,
        settings=load_settings(
            environment=RuntimeEnvironment.TEST,
            nats_enabled=True,
            nats_url="nats://localhost:4222",
        ),
    )
    assert report.binding_verified is True
    assert report.stream == A1_STREAM


def test_verify_nats_readiness() -> None:
    asyncio.run(_verify_readiness())


def test_secret_safe_connection_logging(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)
    secret_url = "nats://user:supersecret@broker.example:4222"
    log_connection_failure(RuntimeError(f"connection failed for {secret_url}"))
    assert secret_url not in caplog.text
    assert "supersecret" not in caplog.text


def test_durable_topology_mismatch_fails_closed() -> None:
    js = FakeJetStream(
        info=_valid_info(stream_name="WRONG"),
        subscription=FakePullSubscription(info=_valid_info(stream_name="WRONG")),
    )

    async def _run() -> None:
        with pytest.raises(ConsumerBindingMismatchError):
            await bind_existing_pull_consumer(
                js,
                settings=load_settings(
                    environment=RuntimeEnvironment.TEST,
                    nats_enabled=True,
                    nats_url="nats://localhost:4222",
                ),
            )

    asyncio.run(_run())
