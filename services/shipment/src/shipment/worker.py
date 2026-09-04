"""Shipment JetStream pickup.fact.accepted consumer worker entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta
from typing import Protocol

import nats

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
from shipment.infrastructure.transport import TransportNotConfiguredError
from shipment.ports.accepted_fact import AcceptedFactUnitOfWork, InboxStorePort

logger = logging.getLogger("shipment.worker")


class WorkerStartupError(RuntimeError):
    """Raised when worker dependencies fail validation."""


class AcceptedFactStore(AcceptedFactUnitOfWork, InboxStorePort, Protocol):
    """Combined UoW + inbox port required by the consumer composition root."""


def build_coordinator(
    settings: ShipmentSettings,
    *,
    transport: DeferredJetStreamTransport,
    store: AcceptedFactStore,
) -> PickupAcceptedFactCoordinator:
    if not settings.nats_enabled:
        raise TransportNotConfiguredError("NATS transport is disabled")
    if settings.persistence_backend is PersistenceBackend.MEMORY:
        if settings.environment is RuntimeEnvironment.PRODUCTION:
            raise WorkerStartupError("in-memory persistence is forbidden in production")
        msg = "worker requires postgres persistence; memory is test-only via coordinator tests"
        raise WorkerStartupError(msg)

    settings.assert_production_gates()
    if not settings.database_url:
        raise WorkerStartupError("database URL is not configured")

    apply_service = NativePickupAcceptedApplyService(store)
    return PickupAcceptedFactCoordinator(
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


async def run_worker(
    settings: ShipmentSettings | None = None,
    *,
    coordinator: PickupAcceptedFactCoordinator | None = None,
    store: AcceptedFactStore | None = None,
) -> None:
    resolved = settings or load_settings()
    loop = asyncio.get_running_loop()
    broker = JetStreamBrokerAckClient(
        loop=loop,
        defer_delay_seconds=resolved.defer_delay_seconds,
    )
    deferred = DeferredJetStreamTransport()
    if coordinator is None:
        if store is None:
            raise WorkerStartupError(
                "accepted-fact store must be injected at the composition root"
            )
        coordinator = build_coordinator(resolved, transport=deferred, store=store)

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
            await nc.close()
        logger.info("shipment_worker_stopped")


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
