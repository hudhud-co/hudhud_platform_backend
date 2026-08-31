"""Audit JetStream observation consumer worker entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

import nats

from audit.application.coordinator import ObservationConsumerCoordinator
from audit.config import AuditSettings, PersistenceBackend, RuntimeEnvironment, load_settings
from audit.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from audit.infrastructure.jetstream.connection import (
    bind_existing_pull_consumer,
    build_nats_connect_options,
    log_connection_failure,
)
from audit.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from audit.infrastructure.jetstream.worker import ObservationPullWorker
from audit.infrastructure.persistence.session import build_engine, build_session_factory
from audit.infrastructure.persistence.sqlalchemy_store import SqlAlchemyAuditStore
from audit.infrastructure.transport import TransportNotConfiguredError

logger = logging.getLogger("audit.worker")


class WorkerStartupError(RuntimeError):
    """Raised when worker dependencies fail validation."""


def build_coordinator(
    settings: AuditSettings,
    *,
    transport: DeferredJetStreamTransport,
) -> ObservationConsumerCoordinator:
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

    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    store = SqlAlchemyAuditStore(session_factory=session_factory)
    coordinator = ObservationConsumerCoordinator(
        unit_of_work=store,
        inbox=store,
        observations=store,
        transport=transport,
        consumer_name=settings.consumer_name,
        handler_version=settings.handler_version,
        processing_owner=settings.processing_owner,
        lease_duration=timedelta(seconds=settings.inbox_lease_seconds),
        max_attempts=settings.inbox_max_attempts,
    )
    return coordinator


async def run_worker(settings: AuditSettings | None = None) -> None:
    resolved = settings or load_settings()
    loop = asyncio.get_running_loop()
    broker = JetStreamBrokerAckClient(
        loop=loop,
        defer_delay_seconds=resolved.defer_delay_seconds,
    )
    deferred = DeferredJetStreamTransport()
    coordinator = build_coordinator(resolved, transport=deferred)

    connect_options = build_nats_connect_options(resolved)
    nc: nats.NATS | None = None
    worker: ObservationPullWorker | None = None
    try:
        nc = await nats.connect(**connect_options)
        js = nc.jetstream()
        subscription, _info = await bind_existing_pull_consumer(js, settings=resolved)
        worker = ObservationPullWorker(
            subscription=subscription,
            coordinator=coordinator,
            broker=broker,
            deferred_transport=deferred,
            pull_batch_size=resolved.pull_batch_size,
            pull_fetch_timeout_seconds=resolved.pull_fetch_timeout_seconds,
            handler_concurrency=resolved.handler_concurrency,
            shutdown_timeout_seconds=resolved.shutdown_timeout_seconds,
        )
        _install_signal_handlers(worker)
        logger.info("audit_worker_started")
        await worker.run_forever()
    except Exception as exc:
        log_connection_failure(exc)
        raise
    finally:
        if nc is not None:
            await nc.close()
        logger.info("audit_worker_stopped")


def _install_signal_handlers(worker: ObservationPullWorker) -> None:
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
