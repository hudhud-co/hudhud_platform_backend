"""Bounded JetStream pull worker with backpressure and graceful drain."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from shipment.application.accepted_fact_coordinator import PickupAcceptedFactCoordinator
from shipment.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from shipment.infrastructure.jetstream.deferred_transport import (
    DeferredJetStreamTransport,
    flush_transport_actions,
)
from shipment.infrastructure.jetstream.delivery import delivery_from_message

logger = logging.getLogger("shipment.pull_worker")


class PickupAcceptedPullWorker:
    """Pull-fetch loop with bounded batch size, concurrency, and explicit backpressure."""

    def __init__(
        self,
        *,
        subscription: Any,
        coordinator: PickupAcceptedFactCoordinator,
        broker: JetStreamBrokerAckClient | None = None,
        deferred_transport: DeferredJetStreamTransport | None = None,
        pull_batch_size: int,
        pull_fetch_timeout_seconds: float,
        handler_concurrency: int,
        shutdown_timeout_seconds: float = 30.0,
        idle_backoff_seconds: float = 0.05,
        fetch_retry_backoff_seconds: float = 1.0,
    ) -> None:
        if pull_batch_size < 1:
            msg = "pull_batch_size must be at least 1"
            raise ValueError(msg)
        if handler_concurrency < 1:
            msg = "handler_concurrency must be at least 1"
            raise ValueError(msg)
        self._subscription = subscription
        self._coordinator = coordinator
        self._broker = broker
        self._deferred_transport = deferred_transport
        self._pull_batch_size = pull_batch_size
        self._pull_fetch_timeout_seconds = pull_fetch_timeout_seconds
        self._semaphore = asyncio.Semaphore(handler_concurrency)
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._idle_backoff_seconds = idle_backoff_seconds
        self._fetch_retry_backoff_seconds = fetch_retry_backoff_seconds
        self._shutdown = asyncio.Event()
        self._active_batch: list[asyncio.Task[None]] = []

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def poll_once(self) -> int:
        """Perform one bounded fetch and await all handlers for that batch."""
        if self._shutdown.is_set():
            return 0

        messages = await self._fetch_batch()
        if not messages:
            return 0

        tasks: list[asyncio.Task[None]] = []
        self._active_batch = tasks
        try:
            for msg in messages:
                if self._shutdown.is_set():
                    break
                await self._semaphore.acquire()
                tasks.append(asyncio.create_task(self._run_handler(msg)))
            if tasks:
                await self._await_batch(tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            self._active_batch = []

        return len(tasks)

    async def _await_batch(self, tasks: list[asyncio.Task[None]]) -> None:
        if self._shutdown.is_set():
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._shutdown_timeout_seconds,
            )
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run_forever(self) -> None:
        """Sequential poll loop with idle and retry backoff."""
        try:
            while not self._shutdown.is_set():
                try:
                    processed = await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — fetch failures are retried
                    if self._shutdown.is_set():
                        break
                    logger.error(
                        "shipment_pull_fetch_failed",
                        extra={"error_code": type(exc).__name__},
                    )
                    await asyncio.sleep(
                        min(self._fetch_retry_backoff_seconds, self._pull_fetch_timeout_seconds)
                    )
                    continue

                if processed == 0:
                    await asyncio.sleep(self._idle_backoff_seconds)
                else:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            self._shutdown.set()
            raise
        finally:
            await self._drain_active_batch()

    async def run(self) -> None:
        """Compatibility alias for :meth:`run_forever`."""
        await self.run_forever()

    async def _drain_active_batch(self) -> None:
        tasks = list(self._active_batch)
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout_seconds)
        _ = done
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _fetch_batch(self) -> list[Any]:
        try:
            messages = await self._subscription.fetch(
                batch=self._pull_batch_size,
                timeout=self._pull_fetch_timeout_seconds,
            )
        except TimeoutError:
            return []
        except Exception as exc:  # noqa: BLE001 — nats TimeoutError varies by version
            if self._is_fetch_timeout(exc):
                return []
            raise
        return list(messages or [])

    async def _run_handler(self, msg: Any) -> None:
        try:
            await self._handle_message(msg)
        finally:
            self._semaphore.release()

    async def _handle_message(self, msg: Any) -> None:
        delivery = delivery_from_message(msg)
        deferred = self._deferred_transport
        try:
            if deferred is not None:
                deferred.pending.clear()
            await asyncio.to_thread(self._coordinator.handle, delivery)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — explicit NAK for unhandled handler failure
            logger.error(
                "shipment_handler_unhandled_error",
                extra={"error_code": type(exc).__name__},
            )
            if self._broker is not None:
                try:
                    await self._broker.apply_nak(delivery)
                except Exception as transport_exc:  # noqa: BLE001 — leave for AckWait
                    logger.error(
                        "shipment_handler_nak_failed",
                        extra={"error_code": type(transport_exc).__name__},
                    )
            return

        if deferred is not None and self._broker is not None and deferred.pending:
            try:
                await flush_transport_actions(self._broker, list(deferred.pending))
            except Exception as transport_exc:  # noqa: BLE001 — leave unacked for AckWait
                logger.error(
                    "shipment_broker_action_failed",
                    extra={"error_code": type(transport_exc).__name__},
                )

    @staticmethod
    def _is_fetch_timeout(exc: BaseException) -> bool:
        name = type(exc).__name__
        return name == "TimeoutError" or "timeout" in name.lower()
