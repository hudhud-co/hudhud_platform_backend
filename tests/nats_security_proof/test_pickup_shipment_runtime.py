"""W18-A Pickup and Shipment NATS JWT/TLS/ACL/rotation proof — one session."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path

import nats
import pytest
from nats.js.api import ConsumerConfig, StreamConfig

from .auth_errors import assert_acl_denied, is_tls_error
from .constants import (
    AUDIT_DURABLE,
    AUDIT_STREAM,
    CONNECT_TIMEOUT_SECONDS,
    IDENTITY_BOOTSTRAP,
    IDENTITY_PICKUP_V1,
    IDENTITY_PICKUP_V2,
    IDENTITY_SHIPMENT_V1,
    IDENTITY_SHIPMENT_V2,
    NATS_SERVICE,
    PICKUP_ACCEPTED_SUBJECT,
    PICKUP_FORBIDDEN_SUBJECT,
    PICKUP_STREAM,
    SHIPMENT_PICKUP_DURABLE,
)
from .helpers import (
    build_tls_nats_url,
    ca_path,
    creds_path,
    discover_host_port,
    revoke_users,
    run_pickup_publish_probe,
    run_shipment_bind_pull_probe,
    wait_for_resolver_propagation,
)
from .nats_client import connect_nats, consumer_info, publish_js

pytestmark = pytest.mark.integration


def _stack_urls(stack: dict) -> tuple[str, Path, Path]:
    nats_port = discover_host_port(NATS_SERVICE, 4222)
    nats_url = build_tls_nats_url(port=nats_port)
    generated_dir: Path = stack["generated_dir"]
    return nats_url, ca_path(generated_dir), generated_dir


def _refresh_nats_url() -> str:
    return build_tls_nats_url(port=discover_host_port(NATS_SERVICE, 4222))


async def _topology_fingerprint(*, nats_url: str, creds_file: Path, ca_file: Path) -> dict:
    nc = await connect_nats(nats_url=nats_url, creds_file=creds_file, ca_file=ca_file)
    try:
        js = nc.jetstream(timeout=2)
        stream = await js.stream_info(PICKUP_STREAM)
        consumer = await js.consumer_info(PICKUP_STREAM, SHIPMENT_PICKUP_DURABLE)
        audit = await js.stream_info(AUDIT_STREAM)
        return {
            "pickup_subjects": list(stream.config.subjects),
            "durable": consumer.config.durable_name,
            "filter": consumer.config.filter_subject,
            "ack_policy": str(consumer.config.ack_policy),
            "audit_name": audit.config.name,
        }
    finally:
        await nc.drain()


def test_w18_pickup_shipment_positive_negative_tls_rotation(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)
    bootstrap = creds_path(generated, IDENTITY_BOOTSTRAP)
    pickup_v1 = creds_path(generated, IDENTITY_PICKUP_V1)
    pickup_v2 = creds_path(generated, IDENTITY_PICKUP_V2)
    shipment_v1 = creds_path(generated, IDENTITY_SHIPMENT_V1)
    shipment_v2 = creds_path(generated, IDENTITY_SHIPMENT_V2)

    before = asyncio.run(
        _topology_fingerprint(nats_url=nats_url, creds_file=bootstrap, ca_file=ca)
    )
    assert before["durable"] == SHIPMENT_PICKUP_DURABLE
    assert before["filter"] == PICKUP_ACCEPTED_SUBJECT

    published = run_pickup_publish_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=pickup_v1,
    )
    assert published["puback_received"] is True
    assert published["stream"] == PICKUP_STREAM
    assert published["subject"] == PICKUP_ACCEPTED_SUBJECT
    assert published["adapter"] == "JetStreamPublisherAdapter"

    consumed = run_shipment_bind_pull_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=shipment_v1,
    )
    assert consumed["binding_verified"] is True
    assert consumed["acked"] == 1
    assert consumed["stream"] == PICKUP_STREAM
    assert consumed["durable"] == SHIPMENT_PICKUP_DURABLE
    assert consumed["subject"] == PICKUP_ACCEPTED_SUBJECT

    after = asyncio.run(
        _topology_fingerprint(nats_url=nats_url, creds_file=bootstrap, ca_file=ca)
    )
    assert after == before

    asyncio.run(
        _negative_acl_batch(
            nats_url=nats_url,
            ca=ca,
            pickup_creds=pickup_v1,
            shipment_creds=shipment_v1,
            bootstrap_creds=bootstrap,
        )
    )
    asyncio.run(_tls_batch(nats_url=nats_url, ca=ca, generated=generated, pickup_creds=pickup_v1))

    for identity_creds in (pickup_v1, pickup_v2):
        overlap = asyncio.run(
            publish_js(
                nats_url=nats_url,
                creds_file=identity_creds,
                ca_file=ca,
                subject=PICKUP_ACCEPTED_SUBJECT,
                payload=b"rotation-overlap",
            )
        )
        assert overlap["stream"] == PICKUP_STREAM

    for identity_creds in (shipment_v1, shipment_v2):
        info = asyncio.run(
            consumer_info(
                nats_url=nats_url,
                creds_file=identity_creds,
                ca_file=ca,
                stream=PICKUP_STREAM,
                durable=SHIPMENT_PICKUP_DURABLE,
            )
        )
        assert info["durable"] == SHIPMENT_PICKUP_DURABLE

    revoke_users(IDENTITY_PICKUP_V1, IDENTITY_SHIPMENT_V1, restart=True)
    wait_for_resolver_propagation()
    nats_url = _refresh_nats_url()

    with pytest.raises(Exception) as pickup_revoked:
        asyncio.run(
            connect_nats(
                nats_url=nats_url,
                creds_file=pickup_v1,
                ca_file=ca,
                connect_timeout=2.0,
            )
        )
    assert_acl_denied(pickup_revoked.value)

    with pytest.raises(Exception) as shipment_revoked:
        asyncio.run(
            connect_nats(
                nats_url=nats_url,
                creds_file=shipment_v1,
                ca_file=ca,
                connect_timeout=2.0,
            )
        )
    assert_acl_denied(shipment_revoked.value)

    still_pickup = asyncio.run(
        publish_js(
            nats_url=nats_url,
            creds_file=pickup_v2,
            ca_file=ca,
            subject=PICKUP_ACCEPTED_SUBJECT,
            payload=b"rotation-v2-active",
        )
    )
    assert still_pickup["stream"] == PICKUP_STREAM

    still_shipment = asyncio.run(
        consumer_info(
            nats_url=nats_url,
            creds_file=shipment_v2,
            ca_file=ca,
            stream=PICKUP_STREAM,
            durable=SHIPMENT_PICKUP_DURABLE,
        )
    )
    assert still_shipment["durable"] == SHIPMENT_PICKUP_DURABLE

    intact = asyncio.run(
        _topology_fingerprint(nats_url=nats_url, creds_file=bootstrap, ca_file=ca)
    )
    assert intact == before


async def _negative_acl_batch(
    *,
    nats_url: str,
    ca: Path,
    pickup_creds: Path,
    shipment_creds: Path,
    bootstrap_creds: Path,
) -> None:
    pickup_nc = await connect_nats(nats_url=nats_url, creds_file=pickup_creds, ca_file=ca)
    try:
        js = pickup_nc.jetstream(timeout=1)
        with pytest.raises(Exception) as exc:
            await js.publish(PICKUP_FORBIDDEN_SUBJECT, b"x", timeout=1)
        assert_acl_denied(exc.value)
        with pytest.raises(Exception) as exc:
            await js.add_stream(StreamConfig(name="FORBIDDEN_PICKUP", subjects=["forbidden.>"]))
        assert_acl_denied(exc.value)
        with pytest.raises(Exception) as exc:
            await js.add_consumer(
                PICKUP_STREAM,
                ConsumerConfig(
                    durable_name="forbidden_pickup_consumer",
                    filter_subject=PICKUP_ACCEPTED_SUBJECT,
                ),
            )
        assert_acl_denied(exc.value)
        with pytest.raises(Exception) as exc:
            await js.consumer_info(PICKUP_STREAM, SHIPMENT_PICKUP_DURABLE)
        assert_acl_denied(exc.value)
    finally:
        await pickup_nc.close()

    shipment_nc = await connect_nats(nats_url=nats_url, creds_file=shipment_creds, ca_file=ca)
    try:
        js = shipment_nc.jetstream(timeout=1)
        with pytest.raises(Exception) as exc:
            await js.publish(PICKUP_ACCEPTED_SUBJECT, b"forbidden", timeout=1)
        assert_acl_denied(exc.value)
        with pytest.raises(Exception) as exc:
            await js.consumer_info(AUDIT_STREAM, AUDIT_DURABLE)
        assert_acl_denied(exc.value)
        with pytest.raises(Exception) as exc:
            await js.add_consumer(
                PICKUP_STREAM,
                ConsumerConfig(
                    durable_name="forbidden_shipment_consumer",
                    filter_subject=PICKUP_ACCEPTED_SUBJECT,
                ),
            )
        assert_acl_denied(exc.value)
        with pytest.raises(Exception) as exc:
            await js.add_stream(StreamConfig(name="FORBIDDEN_SHIPMENT", subjects=["forbidden.>"]))
        assert_acl_denied(exc.value)
        with pytest.raises(Exception) as exc:
            await js.stream_info(AUDIT_STREAM)
        assert_acl_denied(exc.value)
    finally:
        await shipment_nc.close()

    bootstrap_nc = await connect_nats(nats_url=nats_url, creds_file=bootstrap_creds, ca_file=ca)
    try:
        js = bootstrap_nc.jetstream(timeout=1)
        with pytest.raises(Exception) as exc:
            await js.publish(PICKUP_ACCEPTED_SUBJECT, b"forbidden", timeout=1)
        assert_acl_denied(exc.value)
    finally:
        await bootstrap_nc.close()


async def _tls_batch(*, nats_url: str, ca: Path, generated: Path, pickup_creds: Path) -> None:
    nc = await connect_nats(nats_url=nats_url, creds_file=pickup_creds, ca_file=ca)
    await nc.flush()
    await nc.drain()

    plaintext_url = f"nats://127.0.0.1:{discover_host_port(NATS_SERVICE, 4222)}"
    with pytest.raises(Exception) as exc:
        await connect_nats(
            nats_url=plaintext_url,
            creds_file=pickup_creds,
            ca_file=ca,
            allow_plaintext=True,
        )
    message = str(exc.value).lower()
    assert is_tls_error(exc.value) or "connection" in message or "certificate" in message

    wrong_ca = generated / "ca" / "wrong-ca-pickup.pem"
    wrong_ca.write_text("-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----\n")
    with pytest.raises(Exception) as exc:
        await connect_nats(nats_url=nats_url, creds_file=pickup_creds, ca_file=wrong_ca)
    assert is_tls_error(exc.value)

    context = ssl.create_default_context(cafile=str(ca))
    with pytest.raises(Exception) as exc:
        await asyncio.wait_for(
            nats.connect(
                servers=[nats_url],
                user_credentials=str(pickup_creds),
                tls=context,
                tls_hostname="wrong-hostname.example",
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
    assert is_tls_error(exc.value)
