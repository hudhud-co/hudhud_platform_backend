"""Bind-only Shipment poll_once using the worker composition root."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os

import nats
from shipment.config import PersistenceBackend, RuntimeEnvironment, load_settings
from shipment.domain.errors import RetryableHandlerError
from shipment.domain.types import Delivery
from shipment.infrastructure.jetstream.binding import verify_consumer_info
from shipment.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from shipment.infrastructure.jetstream.connection import (
    bind_existing_pull_consumer,
    build_nats_connect_options,
)
from shipment.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from shipment.infrastructure.jetstream.worker import PickupAcceptedPullWorker
from shipment.worker import build_coordinator


class FailFirstAckBroker(JetStreamBrokerAckClient):
    def __init__(self, *, loop: asyncio.AbstractEventLoop, defer_delay_seconds: float) -> None:
        super().__init__(loop=loop, defer_delay_seconds=defer_delay_seconds)
        self.actions: list[str] = []
        self._failed_once = False

    async def apply_ack(self, delivery: Delivery) -> None:
        if not self._failed_once and os.environ.get("SHIPMENT_FAIL_FIRST_ACK") == "1":
            self._failed_once = True
            self.actions.append("ack_failed")
            await super().apply_nak(delivery)
            self.actions.append("nak")
            return
        await super().apply_ack(delivery)
        self.actions.append("ack")

    async def apply_nak(self, delivery: Delivery) -> None:
        await super().apply_nak(delivery)
        self.actions.append("nak")

    async def apply_defer(self, delivery: Delivery) -> None:
        await super().apply_defer(delivery)
        self.actions.append("defer")


def _wrap_quarantine_persist(coordinator: object) -> None:
    inbox = coordinator._inbox  # type: ignore[attr-defined]
    original = inbox.mark_quarantined
    state = {"failed": False}

    def _fail_once(**kwargs: object):
        if not state["failed"]:
            state["failed"] = True
            raise RetryableHandlerError("DB_WRITE_FAILED", "simulated quarantine persist failure")
        return original(**kwargs)

    inbox.mark_quarantined = _fail_once  # type: ignore[method-assign]


async def _poll_once() -> dict[str, object]:
    database_url = os.environ.get("DATABASE_URL")
    nats_url = os.environ.get("NATS_URL")
    if not database_url or not nats_url:
        msg = "DATABASE_URL and NATS_URL are required"
        raise RuntimeError(msg)

    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        database_url=database_url,
        persistence_backend=PersistenceBackend.POSTGRES,
        nats_enabled=True,
        nats_url=nats_url,
        allow_no_auth_local=True,
        pull_batch_size=1,
        pull_fetch_timeout_seconds=2.0,
        handler_concurrency=1,
        processing_owner="pickup-acceptance-proof",
    )
    loop = asyncio.get_running_loop()
    broker = FailFirstAckBroker(loop=loop, defer_delay_seconds=settings.defer_delay_seconds)
    deferred = DeferredJetStreamTransport()
    coordinator, engine = build_coordinator(settings, transport=deferred)
    if os.environ.get("SHIPMENT_FAIL_QUARANTINE_PERSIST") == "1":
        _wrap_quarantine_persist(coordinator)

    connect_options = build_nats_connect_options(settings)
    nc = await nats.connect(**connect_options)
    try:
        js = nc.jetstream()
        subscription, info = await bind_existing_pull_consumer(js, settings=settings)
        binding = verify_consumer_info(info)
        worker = PickupAcceptedPullWorker(
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
            "active_batch": len(worker._active_batch),
            "broker_actions": list(broker.actions),
            "binding_stream": binding.stream,
            "binding_durable": binding.durable_name,
            "binding_filter": binding.filter_subject,
            "binding_ack_policy": binding.ack_policy,
        }
    finally:
        with contextlib.suppress(Exception):
            await nc.close()
        engine.dispose()


def main() -> int:
    result = asyncio.run(_poll_once())
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
