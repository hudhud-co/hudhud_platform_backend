"""Audit bind-only pull adapter poll_once probe with optional first-ACK failure."""

from __future__ import annotations

import asyncio
import json
import os

import nats
from audit.config import PersistenceBackend, RuntimeEnvironment, load_settings
from audit.domain.types import Delivery
from audit.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from audit.infrastructure.jetstream.connection import (
    bind_existing_pull_consumer,
    build_nats_connect_options,
)
from audit.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from audit.infrastructure.jetstream.worker import ObservationPullWorker
from audit.worker import build_coordinator


class FailFirstAckBroker(JetStreamBrokerAckClient):
    """Simulate crash after DB commit but before broker ACK."""

    def __init__(self, *, loop: asyncio.AbstractEventLoop, defer_delay_seconds: float) -> None:
        super().__init__(loop=loop, defer_delay_seconds=defer_delay_seconds)
        self._failed_once = False

    async def apply_ack(self, delivery: Delivery) -> None:
        if not self._failed_once and os.environ.get("AUDIT_FAIL_FIRST_ACK") == "1":
            self._failed_once = True
            msg = "simulated crash after commit before ack"
            raise RuntimeError(msg)
        await super().apply_ack(delivery)


async def _poll_once() -> dict[str, object]:
    database_url = os.environ.get("DATABASE_URL")
    nats_url = os.environ.get("NATS_URL")
    if not database_url or not nats_url:
        msg = "DATABASE_URL and NATS_URL are required"
        raise RuntimeError(msg)

    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        database_url=database_url,
        persistence_backend=PersistenceBackend.POSTGRES,
        nats_enabled=True,
        nats_url=nats_url,
        allow_no_auth_local=True,
        pull_batch_size=2,
        pull_fetch_timeout_seconds=2.0,
        handler_concurrency=1,
        processing_owner="obs-proof-audit",
    )

    loop = asyncio.get_running_loop()
    broker = FailFirstAckBroker(loop=loop, defer_delay_seconds=settings.defer_delay_seconds)
    deferred = DeferredJetStreamTransport()
    coordinator = build_coordinator(settings, transport=deferred)

    connect_options = build_nats_connect_options(settings)
    nc = await nats.connect(**connect_options)
    try:
        js = nc.jetstream()
        subscription, _info = await bind_existing_pull_consumer(js, settings=settings)
        worker = ObservationPullWorker(
            subscription=subscription,
            coordinator=coordinator,
            broker=broker,
            deferred_transport=deferred,
            pull_batch_size=settings.pull_batch_size,
            pull_fetch_timeout_seconds=settings.pull_fetch_timeout_seconds,
            handler_concurrency=settings.handler_concurrency,
            shutdown_timeout_seconds=5.0,
        )
        processed = await asyncio.wait_for(worker.poll_once(), timeout=10.0)
        return {
            "processed": processed,
            "handler_concurrency": settings.handler_concurrency,
            "pull_batch_size": settings.pull_batch_size,
        }
    finally:
        await nc.close()


def main() -> int:
    result = asyncio.run(_poll_once())
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
