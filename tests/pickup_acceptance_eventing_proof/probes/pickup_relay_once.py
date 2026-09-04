"""Run one Pickup outbox relay tick against live JetStream."""

from __future__ import annotations

import contextlib
import json
import os
import sys

from pickup.application.publisher import OutboxPublisher
from pickup.application.relay import OutboxRelayWorker, RelayWorkerSettings
from pickup.config import load_settings
from pickup.infrastructure.nats.client import build_live_nats_client
from pickup.infrastructure.nats.protocols import JetStreamPubAck
from pickup.infrastructure.nats.publisher import JetStreamPublisherAdapter
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyOutboxRelayStore


class _RecordingClient:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.last_ack: JetStreamPubAck | None = None

    def publish(
        self, *, subject: str, payload: bytes, msg_id: str, timeout: float
    ) -> JetStreamPubAck:
        ack = self._inner.publish(  # type: ignore[attr-defined]
            subject=subject,
            payload=payload,
            msg_id=msg_id,
            timeout=timeout,
        )
        self.last_ack = ack
        return ack

    def ping(self) -> bool:
        return self._inner.ping()  # type: ignore[attr-defined]

    def drain(self) -> None:
        self._inner.drain()  # type: ignore[attr-defined]

    def close(self) -> None:
        self._inner.close()  # type: ignore[attr-defined]


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    nats_url = os.environ.get("NATS_URL")
    if not database_url or not nats_url:
        print("DATABASE_URL and NATS_URL are required", file=sys.stderr)
        return 1

    settings = load_settings(
        relay_enabled=True,
        database_url=database_url,
        nats_url=nats_url,
        nats_dev_no_auth=True,
        relay_batch_size=10,
        relay_poll_interval_seconds=0.1,
    )
    engine = build_engine(database_url)
    nats_client = None
    try:
        store = SqlAlchemyOutboxRelayStore(session_factory=build_session_factory(engine))
        live = build_live_nats_client(settings)
        live.connect()
        nats_client = live
        recording = _RecordingClient(live)
        adapter = JetStreamPublisherAdapter(
            recording,  # type: ignore[arg-type]
            publish_timeout_seconds=settings.relay_publish_timeout_seconds,
            transport_max_msg_bytes=settings.relay_transport_max_msg_bytes,
        )
        publisher = OutboxPublisher(
            outbox_store=store,
            publisher=adapter,
            owner_id=settings.relay_owner_id,
            batch_size=settings.relay_batch_size,
            lease_seconds=settings.outbox_lease_seconds,
            retry_backoff_seconds=settings.outbox_retry_backoff_seconds,
        )
        worker = OutboxRelayWorker(
            publisher=publisher,
            nats_adapter=adapter,
            settings=RelayWorkerSettings(
                batch_size=settings.relay_batch_size,
                poll_interval_seconds=settings.relay_poll_interval_seconds,
                lease_seconds=settings.outbox_lease_seconds,
                owner_id=settings.relay_owner_id,
            ),
        )
        outcome = worker.run_once()
        ack = recording.last_ack
        print(
            json.dumps(
                {
                    "published_count": outcome.published_count,
                    "retry_count": outcome.retry_count,
                    "quarantined_count": outcome.quarantined_count,
                    "puback_received": ack is not None,
                    "puback_stream": ack.stream if ack is not None else None,
                    "puback_duplicate": bool(ack.duplicate) if ack is not None else False,
                }
            )
        )
        return 0
    finally:
        if nats_client is not None:
            with contextlib.suppress(Exception):
                nats_client.drain()
            with contextlib.suppress(Exception):
                nats_client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
