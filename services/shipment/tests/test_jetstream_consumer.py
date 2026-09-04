"""JetStream pull consumer adapter tests — fake NATS and fake persistence only."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from conftest import (
    EVENT_ID,
    make_delivery_from_envelope,
    seed_created_shipment,
    valid_envelope,
)
from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from shipment.application.accepted_fact_apply import NativePickupAcceptedApplyService
from shipment.application.accepted_fact_coordinator import PickupAcceptedFactCoordinator
from shipment.application.readiness import evaluate_readiness
from shipment.config import PersistenceBackend, RuntimeEnvironment, load_settings
from shipment.domain.contract import (
    PICKUP_ACCEPTED_DURABLE_CONSUMER,
    PICKUP_ACCEPTED_STREAM,
    PICKUP_ACCEPTED_SUBJECT,
)
from shipment.domain.types import Delivery, InboxRow
from shipment.infrastructure.accepted_fact_memory import (
    MemoryAcceptedFactStore,
    RecordingTransport,
    SimulatedCrash,
)
from shipment.infrastructure.jetstream.binding import (
    EXPECTED_ACK_POLICY,
    ConsumerBindingMismatchError,
    expected_consumer_binding,
    verify_consumer_info,
)
from shipment.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from shipment.infrastructure.jetstream.connection import (
    NatsAuthRequiredError,
    bind_existing_pull_consumer,
    build_nats_connect_options,
    log_connection_failure,
    verify_nats_readiness,
)
from shipment.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from shipment.infrastructure.jetstream.delivery import delivery_from_message
from shipment.infrastructure.jetstream.worker import PickupAcceptedPullWorker


@dataclass
class FakeMsg:
    data: bytes
    subject: str
    stream_seq: int | None = 11
    nats_msg_id: str | None = None
    actions: list[str] = field(default_factory=list)
    nak_delay: timedelta | None = None
    ack_fail: bool = False

    @property
    def metadata(self) -> SimpleNamespace:
        return SimpleNamespace(sequence=SimpleNamespace(stream=self.stream_seq))

    @property
    def headers(self) -> dict[str, str] | None:
        if self.nats_msg_id is None:
            return None
        return {"Nats-Msg-Id": self.nats_msg_id}

    async def ack(self) -> None:
        if self.ack_fail:
            raise RuntimeError("broker ack failed")
        self.actions.append("ack")

    async def nak(self, delay: timedelta | None = None) -> None:
        self.nak_delay = delay
        self.actions.append("nak")


@dataclass
class FakeConsumerConfig:
    filter_subject: str
    ack_policy: str = EXPECTED_ACK_POLICY


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

    async def add_stream(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append(("add_stream",))
        raise AssertionError("topology mutation forbidden")

    async def update_consumer(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append(("update_consumer",))
        raise AssertionError("topology mutation forbidden")


def _valid_info(**overrides: object) -> FakeConsumerInfo:
    expected = expected_consumer_binding()
    values = {
        "stream_name": expected.stream,
        "name": expected.durable_name,
        "config": FakeConsumerConfig(
            filter_subject=expected.filter_subject,
            ack_policy=expected.ack_policy,
        ),
    }
    values.update(overrides)
    return FakeConsumerInfo(**values)  # type: ignore[arg-type]


def _make_coordinator(
    store: MemoryAcceptedFactStore,
    *,
    transport: RecordingTransport | DeferredJetStreamTransport,
    now: object | None = None,
) -> PickupAcceptedFactCoordinator:
    apply_service = NativePickupAcceptedApplyService(store)
    clock = (lambda: now) if now is not None else None  # type: ignore[misc, return-value]
    return PickupAcceptedFactCoordinator(
        unit_of_work=store,
        inbox=store,
        transport=transport,  # type: ignore[arg-type]
        apply_service=apply_service,
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        handler_version="0.1.0",
        processing_owner="test-worker",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=clock,  # type: ignore[arg-type]
    )


def test_expected_binding_matches_topology() -> None:
    binding = expected_consumer_binding()
    assert binding.stream == "HUDHUD_PICKUP"
    assert binding.durable_name == "shipment_pickup_facts_v1"
    assert binding.filter_subject == "hudhud.pickup.pickup.fact.accepted.v1"
    assert binding.ack_policy == "explicit"


def test_verify_consumer_info_accepts_exact_binding() -> None:
    verify_consumer_info(_valid_info())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stream_name", "HUDHUD_SHIPMENT"),
        ("name", "other_consumer"),
        ("config", FakeConsumerConfig(filter_subject="hudhud.pickup.>")),
        ("config", FakeConsumerConfig(filter_subject=PICKUP_ACCEPTED_SUBJECT, ack_policy="none")),
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
        assert bound_info.name == PICKUP_ACCEPTED_DURABLE_CONSUMER
        assert js.calls == [
            ("consumer_info", PICKUP_ACCEPTED_STREAM, PICKUP_ACCEPTED_DURABLE_CONSUMER),
            ("pull_subscribe_bind", PICKUP_ACCEPTED_DURABLE_CONSUMER, PICKUP_ACCEPTED_STREAM),
        ]

    asyncio.run(_run())


def test_delivery_from_message_extracts_metadata_without_payload_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    envelope = valid_envelope()
    msg = FakeMsg(
        data=json.dumps(envelope).encode(),
        subject=PICKUP_ACCEPTED_SUBJECT,
        stream_seq=42,
        nats_msg_id="broker-msg-1",
    )
    delivery = delivery_from_message(msg)
    assert delivery.subject == PICKUP_ACCEPTED_SUBJECT
    assert delivery.stream == PICKUP_ACCEPTED_STREAM
    assert delivery.consumer_name == PICKUP_ACCEPTED_DURABLE_CONSUMER
    assert delivery.jetstream_seq == 42
    assert delivery.nats_msg_id == "broker-msg-1"
    assert delivery.transport_handle is msg
    for record in caplog.records:
        assert envelope["payload"]["scanned_identifier"] not in record.getMessage()


def test_broker_ack_nak_defer_mapping() -> None:
    async def _run() -> None:
        loop = asyncio.get_running_loop()
        broker = JetStreamBrokerAckClient(loop=loop, defer_delay_seconds=7.0)
        msg = FakeMsg(data=b"{}", subject=PICKUP_ACCEPTED_SUBJECT)
        delivery = delivery_from_message(msg)
        await broker.apply_ack(delivery)
        await broker.apply_nak(delivery)
        await broker.apply_defer(delivery)
        assert msg.actions == ["ack", "nak", "nak"]
        assert msg.nak_delay == timedelta(seconds=7.0)

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("setup", "expected_action"),
    [
        ("valid", "ack"),
        ("duplicate", "ack"),
        ("retryable", "nak"),
        ("defer", "defer"),
        ("expired_lease", "ack"),
        ("quarantine", "ack"),
    ],
)
def test_end_to_end_transport_mapping_via_worker(
    setup: str,
    expected_action: str,
    now: object,
) -> None:
    store = MemoryAcceptedFactStore()
    seed_created_shipment(store)
    if setup == "retryable":
        store.fail_next_apply = True

    if setup == "duplicate":
        seed = _make_coordinator(store, transport=RecordingTransport(store), now=now)
        seed.handle(make_delivery_from_envelope())
        store.actions.clear()
        store.transport_actions.clear()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        broker = JetStreamBrokerAckClient(loop=loop, defer_delay_seconds=5.0)
        deferred = DeferredJetStreamTransport()
        coordinator = _make_coordinator(store, transport=deferred, now=now)

        envelope = valid_envelope()
        msg = FakeMsg(data=json.dumps(envelope).encode(), subject=PICKUP_ACCEPTED_SUBJECT)

        if setup == "defer":
            store.seed_inbox(
                InboxRow(
                    id=UUID("11111111-1111-4111-8111-111111111111"),
                    consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
                    event_id=EVENT_ID,
                    event_type="pickup.fact.accepted",
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
                    jetstream_stream=PICKUP_ACCEPTED_STREAM,
                    jetstream_seq=1,
                    correlation_id=None,
                    nats_msg_id=None,
                )
            )
        if setup == "expired_lease":
            store.seed_inbox(
                InboxRow(
                    id=UUID("22222222-2222-4222-8222-222222222222"),
                    consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
                    event_id=EVENT_ID,
                    event_type="pickup.fact.accepted",
                    event_version=1,
                    status=InboxStatus.PROCESSING,
                    processing_owner="crashed-replica",
                    processing_lease_until=now - timedelta(seconds=5),  # type: ignore[operator]
                    handler_version="0.1.0",
                    attempt_count=1,
                    first_received_at=now,  # type: ignore[arg-type]
                    last_received_at=now,  # type: ignore[arg-type]
                    processed_at=None,
                    quarantined_at=None,
                    last_error_code=None,
                    last_error_message=None,
                    jetstream_stream=PICKUP_ACCEPTED_STREAM,
                    jetstream_seq=1,
                    correlation_id=None,
                    nats_msg_id=None,
                )
            )
        if setup == "quarantine":
            msg = FakeMsg(data=b"not-json", subject=PICKUP_ACCEPTED_SUBJECT)

        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[[msg]],
            block_when_exhausted=False,
        )
        worker = PickupAcceptedPullWorker(
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
        assert deferred.pending[-1].action.value == expected_action
        if expected_action == "defer":
            assert msg.actions[-1] == "nak"
            assert msg.nak_delay == timedelta(seconds=5.0)
        else:
            assert msg.actions[-1] == expected_action

    asyncio.run(_run())


def test_crash_after_commit_before_ack(now: object) -> None:
    store = MemoryAcceptedFactStore()
    seed_created_shipment(store)
    store.crash_before_ack = True
    coordinator = _make_coordinator(store, transport=RecordingTransport(store), now=now)
    with pytest.raises(SimulatedCrash):
        coordinator.handle(make_delivery_from_envelope())
    assert "commit" in store.actions
    assert "ack" not in store.actions


def test_broker_action_failure_leaves_unacked() -> None:
    async def _run() -> None:
        store = MemoryAcceptedFactStore()
        seed_created_shipment(store)
        loop = asyncio.get_running_loop()
        broker = JetStreamBrokerAckClient(loop=loop, defer_delay_seconds=5.0)
        deferred = DeferredJetStreamTransport()
        coordinator = _make_coordinator(store, transport=deferred)
        msg = FakeMsg(
            data=json.dumps(valid_envelope()).encode(),
            subject=PICKUP_ACCEPTED_SUBJECT,
            ack_fail=True,
        )
        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[[msg]],
            block_when_exhausted=False,
        )
        worker = PickupAcceptedPullWorker(
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
        assert msg.actions == []
        assert deferred.pending[-1].action is JetStreamConsumerAction.ACK

    asyncio.run(_run())


def test_poll_once_processes_single_batch(now: object) -> None:
    async def _run() -> None:
        store = MemoryAcceptedFactStore()
        seed_created_shipment(store)
        coordinator = _make_coordinator(store, transport=RecordingTransport(store), now=now)
        msg = FakeMsg(data=json.dumps(valid_envelope()).encode(), subject=PICKUP_ACCEPTED_SUBJECT)
        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[[msg]],
            block_when_exhausted=False,
        )
        worker = PickupAcceptedPullWorker(
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
        assert store.committed_inbox(
            consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
            event_id=EVENT_ID,
        ).status is InboxStatus.PROCESSED

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

        worker = PickupAcceptedPullWorker(
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
        msg = FakeMsg(data=json.dumps(valid_envelope()).encode(), subject=PICKUP_ACCEPTED_SUBJECT)

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
        worker = PickupAcceptedPullWorker(
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

        msgs = [FakeMsg(data=b"{}", subject=PICKUP_ACCEPTED_SUBJECT) for _ in range(6)]
        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[msgs],
            block_when_exhausted=False,
        )
        worker = PickupAcceptedPullWorker(
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


def test_worker_run_forever_cancellation_safe_shutdown(now: object) -> None:
    async def _run() -> None:
        store = MemoryAcceptedFactStore()
        seed_created_shipment(store)
        coordinator = _make_coordinator(store, transport=RecordingTransport(store), now=now)
        msg = FakeMsg(data=json.dumps(valid_envelope()).encode(), subject=PICKUP_ACCEPTED_SUBJECT)
        info = _valid_info()
        subscription = FakePullSubscription(info=info, batches=[[msg]])
        worker = PickupAcceptedPullWorker(
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
            assert store.committed_inbox(
                consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
                event_id=EVENT_ID,
            ).status is InboxStatus.PROCESSED
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


def test_graceful_shutdown_drains_active_batch(now: object) -> None:
    async def _run() -> None:
        store = MemoryAcceptedFactStore()
        seed_created_shipment(store)
        started = asyncio.Event()
        inner = _make_coordinator(store, transport=RecordingTransport(store), now=now)

        class SlowCoordinator:
            def handle(self, delivery: Delivery) -> None:
                started.set()
                time.sleep(0.08)
                inner.handle(delivery)

        msg = FakeMsg(data=json.dumps(valid_envelope()).encode(), subject=PICKUP_ACCEPTED_SUBJECT)
        info = _valid_info()
        subscription = FakePullSubscription(
            info=info,
            batches=[[msg]],
            block_when_exhausted=False,
        )
        worker = PickupAcceptedPullWorker(
            subscription=subscription,
            coordinator=SlowCoordinator(),  # type: ignore[arg-type]
            pull_batch_size=1,
            pull_fetch_timeout_seconds=0.1,
            handler_concurrency=1,
            shutdown_timeout_seconds=1.0,
            idle_backoff_seconds=0.01,
        )
        task = asyncio.create_task(worker.run_forever())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        worker.request_shutdown()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2.0)
        inbox = store.committed_inbox(
            consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
            event_id=EVENT_ID,
        )
        assert inbox is not None
        assert inbox.status is InboxStatus.PROCESSED

    asyncio.run(_run())


def test_production_requires_credentials() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://broker.example:4222",
        adr_0010_credentials_configured=False,
    )
    with pytest.raises(NatsAuthRequiredError):
        build_nats_connect_options(settings)


def test_production_allows_adr_gate() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://broker.example:4222",
        adr_0010_credentials_configured=True,
        nats_tls_enabled=True,
    )
    options = build_nats_connect_options(settings)
    assert options["servers"] == ["nats://broker.example:4222"]
    tls_context = options["tls"]
    assert isinstance(tls_context, ssl.SSLContext)
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.check_hostname is True


def test_production_requires_tls() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://localhost/shipment",
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
        database_url="postgresql+psycopg://localhost/shipment",
        persistence_backend=PersistenceBackend.MEMORY,
        adr_0010_credentials_configured=True,
    )
    report = evaluate_readiness(
        settings=settings,
        engine=None,
        persistence_wired=True,
        authorization_adapter_ready=True,
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
        authorization_adapter_ready=True,
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
    assert report.stream == PICKUP_ACCEPTED_STREAM


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


def test_no_topology_mutation_methods_used() -> None:
    info = _valid_info()
    subscription = FakePullSubscription(info=info, batches=[[]])
    js = FakeJetStream(info=info, subscription=subscription)

    async def _run() -> None:
        await bind_existing_pull_consumer(
            js,
            settings=load_settings(
                environment=RuntimeEnvironment.TEST,
                nats_enabled=True,
                nats_url="nats://localhost:4222",
            ),
        )
        assert "add_consumer" not in {call[0] for call in js.calls}
        assert "add_stream" not in {call[0] for call in js.calls}
        assert "update_consumer" not in {call[0] for call in js.calls}
        assert "pull_subscribe" not in {call[0] for call in js.calls}

    asyncio.run(_run())
