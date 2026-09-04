"""Bounded outbox relay worker loop with graceful shutdown."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from pickup.application.publisher import OutboxPublisher, PublishBatchOutcome
from pickup.domain.sanitize import sanitize_error_message
from pickup.infrastructure.nats.publisher import JetStreamPublisherAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RelayWorkerSettings:
    """Provisional relay tuning — override via PickupSettings."""

    batch_size: int
    poll_interval_seconds: float
    lease_seconds: int
    owner_id: str


class OutboxRelayWorker:
    """Polls claimed outbox batches without holding DB transactions during publish."""

    def __init__(
        self,
        *,
        publisher: OutboxPublisher,
        nats_adapter: JetStreamPublisherAdapter,
        settings: RelayWorkerSettings,
    ) -> None:
        self._publisher = publisher
        self._nats = nats_adapter
        self._settings = settings
        self._stop = threading.Event()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def request_stop(self) -> None:
        self._stop.set()

    def run_until_stopped(self) -> None:
        """Run the relay loop until stop is requested."""
        self._running = True
        try:
            while not self._stop.is_set():
                outcome = self._publisher.publish_pending()
                if self._is_idle_batch(outcome):
                    self._stop.wait(timeout=self._settings.poll_interval_seconds)
                else:
                    self._stop.wait(timeout=0)
        except Exception as exc:
            logger.error("relay worker stopped after error: %s", sanitize_error_message(str(exc)))
            raise
        finally:
            self._running = False
            self._nats.drain()
            self._nats.close()

    def run_once(self) -> PublishBatchOutcome:
        """Process a single relay tick — used in tests."""
        return self._publisher.publish_pending()

    @staticmethod
    def _is_idle_batch(outcome: PublishBatchOutcome) -> bool:
        return (
            outcome.published_count == 0
            and outcome.retry_count == 0
            and outcome.quarantined_count == 0
        )


def sleep_with_stop(stop_event: threading.Event, seconds: float) -> None:
    """Interruptible sleep for shutdown paths."""
    deadline = time.monotonic() + seconds
    while not stop_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        stop_event.wait(timeout=min(remaining, 0.25))
