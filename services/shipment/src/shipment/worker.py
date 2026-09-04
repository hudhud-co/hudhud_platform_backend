"""Shipment JetStream pickup.fact.accepted consumer worker entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

import nats
from sqlalchemy.engine import Engine

from shipment.application.accepted_fact_apply import NativePickupAcceptedApplyService
from shipment.application.accepted_fact_coordinator import PickupAcceptedFactCoordinator
from shipment.config import PersistenceBackend, RuntimeEnvironment, ShipmentSettings, load_settings
from shipment.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from shipment.infrastructure.jetstream.connection import (
    bind_existing_pull_consumer,
    build_nats_connect_options,
    log_connection_failure,
)
from shipment.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from shipment.infrastructure.jetstream.worker import PickupAcceptedPullWorker
from shipment.infrastructure.persistence.accepted_fact_uow import SqlAlchemyAcceptedFactStore
from shipment.infrastructure.persistence.session import (
    assert_migrations_applied,
    build_engine,
    build_session_factory,
    ping_database,
)
from shipment.infrastructure.transport import TransportNotConfiguredError

logger = logging.getLogger("shipment.worker")


class WorkerStartupError(RuntimeError):
    """Raised when worker dependencies fail validation."""


def build_coordinator(
    settings: ShipmentSettings,
    *,
    transport: DeferredJetStreamTransport,
) -> tuple[PickupAcceptedFactCoordinator, Engine]:
    if not settings.nats_enabled:
        raise TransportNotConfiguredError("NATS transport is disabled")
    if settings.persistence_backend is PersistenceBackend.MEMORY:
        if settings.environment in {
            RuntimeEnvironment.STAGING,
            RuntimeEnvironment.PRODUCTION,
        }:
            raise WorkerStartupError("in-memory persistence is forbidden in staging/production")
        msg = "worker requires postgres persistence; memory is test-only via coordinator tests"
        raise WorkerStartupError(msg)

    settings.assert_production_gates()
    if not settings.database_url:
        raise WorkerStartupError("database URL is not configured")

    engine = build_engine(settings.database_url)
    if not ping_database(engine):
        engine.dispose()
        raise WorkerStartupError("database is not reachable")
    try:
        assert_migrations_applied(engine)
    except RuntimeError as exc:
        engine.dispose()
        raise WorkerStartupError("database migrations are unavailable") from exc

    session_factory = build_session_factory(engine)
    store = SqlAlchemyAcceptedFactStore(session_factory=session_factory)
    apply_service = NativePickupAcceptedApplyService(store)
    coordinator = PickupAcceptedFactCoordinator(
        unit_of_work=store,
        inbox=store,
        transport=transport,
        apply_service=apply_service,
        consumer_name=settings.consumer_name,
        handler_version=settings.handler_version,
        processing_owner=settings.processing_owner,
        lease_duration=timedelta(seconds=settings.inbox_lease_seconds),
        max_attempts=settings.inbox_max_attempts,
    )
    return coordinator, engine


async def run_worker(
    settings: ShipmentSettings | None = None,
    *,
    coordinator: PickupAcceptedFactCoordinator | None = None,
) -> None:
    resolved = settings or load_settings()
    loop = asyncio.get_running_loop()
    broker = JetStreamBrokerAckClient(
        loop=loop,
        defer_delay_seconds=resolved.defer_delay_seconds,
    )
    deferred = DeferredJetStreamTransport()
    owned_engine: Engine | None = None
    if coordinator is None:
        coordinator, owned_engine = build_coordinator(resolved, transport=deferred)

    connect_options = build_nats_connect_options(resolved)
    nc: nats.NATS | None = None
    worker: PickupAcceptedPullWorker | None = None
    try:
        nc = await nats.connect(**connect_options)
        js = nc.jetstream()
        subscription, _info = await bind_existing_pull_consumer(js, settings=resolved)
        worker = PickupAcceptedPullWorker(
            subscription=subscription,
            coordinator=coordinator,
            broker=broker,
            deferred_transport=deferred,
            pull_batch_size=resolved.pull_batch_size,
            pull_fetch_timeout_seconds=resolved.pull_fetch_timeout_seconds,
            handler_concurrency=resolved.handler_concurrency,
            shutdown_timeout_seconds=resolved.shutdown_timeout_seconds,
            idle_backoff_seconds=resolved.idle_backoff_seconds,
            fetch_retry_backoff_seconds=resolved.fetch_retry_backoff_seconds,
        )
        _install_signal_handlers(worker)
        logger.info("shipment_worker_started")
        await worker.run_forever()
    except Exception as exc:
        log_connection_failure(exc)
        raise
    finally:
        if nc is not None:
            await _close_nats(nc)
        if owned_engine is not None:
            owned_engine.dispose()
        logger.info("shipment_worker_stopped")


async def _close_nats(nc: nats.NATS) -> None:
    try:
        if not nc.is_closed:
            await nc.drain()
    except Exception:
        try:
            await nc.close()
        except Exception:
            logger.error("shipment_nats_shutdown_failed", extra={"error_code": "NATS_CLOSE"})


def _install_signal_handlers(worker: PickupAcceptedPullWorker) -> None:
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        worker.request_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            signal.signal(sig, lambda _signum, _frame: worker.request_shutdown())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
