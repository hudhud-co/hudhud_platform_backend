"""Relay runtime entry point — signal handling and graceful shutdown."""

from __future__ import annotations

import logging
import signal
import sys

from legacy_event_bridge.application.publisher import OutboxPublisher
from legacy_event_bridge.application.relay import OutboxRelayWorker, RelayWorkerSettings
from legacy_event_bridge.config import load_settings
from legacy_event_bridge.infrastructure.nats.client import build_live_nats_client
from legacy_event_bridge.infrastructure.nats.publisher import JetStreamPublisherAdapter
from legacy_event_bridge.infrastructure.persistence.postgres_store import SqlAlchemyBridgeStore
from legacy_event_bridge.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()
    settings.assert_production_gates()

    engine = build_engine(settings.database_url)
    store = SqlAlchemyBridgeStore(session_factory=build_session_factory(engine))
    nats_client = build_live_nats_client(settings)
    nats_client.connect()

    nats_adapter = JetStreamPublisherAdapter(
        nats_client,
        publish_timeout_seconds=settings.relay_publish_timeout_seconds,
    )
    publisher = OutboxPublisher(
        outbox_store=store,
        publisher=nats_adapter,
        owner_id=settings.relay_owner_id,
        batch_size=settings.relay_batch_size,
        lease_seconds=settings.outbox_lease_seconds,
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

    logger.info("bridge outbox relay starting")
    try:
        worker.run_until_stopped()
    finally:
        logger.info("bridge outbox relay stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
