"""Relay runtime entry point — signal handling and graceful shutdown."""

from __future__ import annotations

import logging
import signal
import sys

from pickup.application.publisher import OutboxPublisher
from pickup.application.relay import OutboxRelayWorker, RelayWorkerSettings
from pickup.config import load_settings
from pickup.infrastructure.nats.client import build_live_nats_client
from pickup.infrastructure.nats.publisher import JetStreamPublisherAdapter
from pickup.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyOutboxRelayStore

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()
    settings.assert_production_gates()

    if not settings.relay_enabled:
        logger.error("pickup accepted-fact relay is disabled")
        return 1
    if not settings.database_url:
        logger.error("DATABASE_URL is required for the outbox relay")
        return 1

    engine = build_engine(settings.database_url)
    store = SqlAlchemyOutboxRelayStore(session_factory=build_session_factory(engine))
    nats_client = build_live_nats_client(settings)
    nats_client.connect()

    nats_adapter = JetStreamPublisherAdapter(
        nats_client,
        publish_timeout_seconds=settings.relay_publish_timeout_seconds,
        transport_max_msg_bytes=settings.relay_transport_max_msg_bytes,
    )
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=nats_adapter,
        owner_id=settings.relay_owner_id,
        batch_size=settings.relay_batch_size,
        lease_seconds=settings.outbox_lease_seconds,
        retry_backoff_seconds=settings.outbox_retry_backoff_seconds,
    )
    worker_settings = RelayWorkerSettings(
        batch_size=settings.relay_batch_size,
        poll_interval_seconds=settings.relay_poll_interval_seconds,
        lease_seconds=settings.outbox_lease_seconds,
        owner_id=settings.relay_owner_id,
    )
    worker = OutboxRelayWorker(
        publisher=publisher,
        nats_adapter=nats_adapter,
        settings=worker_settings,
    )

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("received signal %s — stopping relay", signum)
        worker.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("pickup accepted-fact outbox relay starting")
    try:
        worker.run_until_stopped()
    finally:
        logger.info("pickup accepted-fact outbox relay stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
