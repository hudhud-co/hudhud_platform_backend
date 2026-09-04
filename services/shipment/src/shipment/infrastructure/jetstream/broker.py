"""JetStream ACK/NAK/DEFER client — no payload logging."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from shipment.domain.types import Delivery


class JetStreamBrokerAckClient:
    """Apply broker actions for a delivery after coordinator commit."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        defer_delay_seconds: float,
    ) -> None:
        self._loop = loop
        self._defer_delay_seconds = defer_delay_seconds

    def ack(self, delivery: Delivery) -> None:
        self._run(self._ack_async(delivery))

    def nak(self, delivery: Delivery) -> None:
        self._run(self._nak_async(delivery))

    def defer(self, delivery: Delivery) -> None:
        self._run(self._defer_async(delivery))

    async def apply_ack(self, delivery: Delivery) -> None:
        await self._ack_async(delivery)

    async def apply_nak(self, delivery: Delivery) -> None:
        await self._nak_async(delivery)

    async def apply_defer(self, delivery: Delivery) -> None:
        await self._defer_async(delivery)

    def _run(self, coro: Any) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            raise RuntimeError(
                "JetStreamBrokerAckClient synchronous methods must run off the worker event loop"
            )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.result()

    async def _ack_async(self, delivery: Delivery) -> None:
        msg = _require_message(delivery)
        await msg.ack()

    async def _nak_async(self, delivery: Delivery) -> None:
        msg = _require_message(delivery)
        await msg.nak()

    async def _defer_async(self, delivery: Delivery) -> None:
        msg = _require_message(delivery)
        delay = timedelta(seconds=self._defer_delay_seconds)
        await msg.nak(delay=delay)


def _require_message(delivery: Delivery) -> Any:
    msg = delivery.transport_handle
    if msg is None:
        msg = "delivery missing JetStream transport handle"
        raise RuntimeError(msg)
    return msg
