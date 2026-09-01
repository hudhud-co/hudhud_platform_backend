"""Runtime NATS JWT/TLS/ACL/rotation/revocation integration proof."""

from __future__ import annotations

import asyncio
import json
import shutil
import ssl
import uuid
from pathlib import Path

import nats
import pytest
from nats.js.api import ConsumerConfig, StreamConfig

from .auth_errors import (
    assert_acl_denied,
    is_tls_error,
    sanitize_error_message,
)
from .constants import (
    A1_SUBJECT,
    A2_SUBJECT,
    AUDIT_DURABLE,
    AUDIT_STREAM,
    CONNECT_TIMEOUT_SECONDS,
    FETCH_TIMEOUT_SECONDS,
    IDENTITY_AUDIT_V1,
    IDENTITY_AUDIT_V2,
    IDENTITY_BOOTSTRAP,
    IDENTITY_BRIDGE_V1,
    IDENTITY_BRIDGE_V2,
    IDENTITY_TRACKING_V1,
    NATS_SERVICE,
    SHIPMENT_STREAM,
    TRACKING_DURABLE,
)
from .helpers import (
    build_tls_nats_url,
    ca_path,
    creds_path,
    discover_host_port,
    extract_generated_material,
    revoke_user,
    run_audit_bind_pull_probe,
    run_audit_readiness_probe,
    run_bridge_publish_probe,
    wait_for_resolver_propagation,
)
from .nats_client import connect_nats, consumer_info, publish_js, pull_and_ack_one

pytestmark = pytest.mark.integration


def _stack_urls(stack: dict) -> tuple[str, Path, Path]:
    nats_port = discover_host_port(NATS_SERVICE, 4222)
    nats_url = build_tls_nats_url(port=nats_port)
    generated_dir: Path = stack["generated_dir"]
    return nats_url, ca_path(generated_dir), generated_dir


def _refresh_nats_url() -> str:
    return build_tls_nats_url(port=discover_host_port(NATS_SERVICE, 4222))


def test_positive_bootstrap_topology_present(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)
    bootstrap_creds = creds_path(generated, IDENTITY_BOOTSTRAP)

    async def _check() -> None:
        nc = await connect_nats(nats_url=nats_url, creds_file=bootstrap_creds, ca_file=ca)
        try:
            js = nc.jetstream()
            shipment = await js.stream_info(SHIPMENT_STREAM)
            audit = await js.stream_info(AUDIT_STREAM)
            tracking = await js.consumer_info(SHIPMENT_STREAM, TRACKING_DURABLE)
            audit_consumer = await js.consumer_info(AUDIT_STREAM, AUDIT_DURABLE)
            assert shipment.config.name == SHIPMENT_STREAM
            assert audit.config.name == AUDIT_STREAM
            assert tracking.config.durable_name == TRACKING_DURABLE
            assert audit_consumer.config.durable_name == AUDIT_DURABLE
        finally:
            await nc.drain()

    asyncio.run(_check())


def test_positive_bridge_publish_a2_puback(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)
    result = run_bridge_publish_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_BRIDGE_V1),
        subject=A2_SUBJECT,
        payload=json.dumps({"proof": "a2", "event_id": str(uuid.uuid4())}),
        msg_id=f"proof-{uuid.uuid4()}",
    )
    assert result["puback_received"] is True
    assert result["stream"] == AUDIT_STREAM


def test_positive_audit_bind_pull_ack(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)
    event_id = str(uuid.uuid4())
    run_bridge_publish_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_BRIDGE_V1),
        subject=A2_SUBJECT,
        payload=json.dumps({"event_id": event_id}),
        msg_id=f"audit-proof-{event_id}",
    )
    result = run_audit_bind_pull_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_AUDIT_V1),
    )
    assert result["binding_verified"] is True
    assert result["acked"] == 1


def test_positive_tracking_pull_ack_only_tracking_durable(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)
    event_id = str(uuid.uuid4())
    asyncio.run(
        publish_js(
            nats_url=nats_url,
            creds_file=creds_path(generated, IDENTITY_BRIDGE_V1),
            ca_file=ca,
            subject=A1_SUBJECT,
            payload=json.dumps({"event_id": event_id}).encode("utf-8"),
            msg_id=f"tracking-proof-{event_id}",
        )
    )
    info = asyncio.run(
        consumer_info(
            nats_url=nats_url,
            creds_file=creds_path(generated, IDENTITY_TRACKING_V1),
            ca_file=ca,
            stream=SHIPMENT_STREAM,
            durable=TRACKING_DURABLE,
        )
    )
    assert info["durable"] == TRACKING_DURABLE
    pulled = asyncio.run(
        pull_and_ack_one(
            nats_url=nats_url,
            creds_file=creds_path(generated, IDENTITY_TRACKING_V1),
            ca_file=ca,
            stream=SHIPMENT_STREAM,
            durable=TRACKING_DURABLE,
            fetch_timeout=FETCH_TIMEOUT_SECONDS,
        )
    )
    assert pulled["acked"] == 1


@pytest.mark.parametrize(
    ("identity", "operation"),
    [
        (IDENTITY_BRIDGE_V1, "bridge_forbidden_subject"),
        (IDENTITY_BRIDGE_V1, "bridge_create_stream"),
        (IDENTITY_AUDIT_V1, "audit_publish_business"),
        (IDENTITY_AUDIT_V1, "audit_access_tracking_durable"),
        (IDENTITY_AUDIT_V1, "audit_create_consumer"),
        (IDENTITY_TRACKING_V1, "tracking_access_audit_durable"),
        (IDENTITY_TRACKING_V1, "tracking_publish_business"),
        (IDENTITY_BOOTSTRAP, "bootstrap_publish_a2"),
        (IDENTITY_AUDIT_V1, "audit_ungranted_js_api"),
    ],
)
def test_negative_acl_denials(
    nats_security_stack: dict,
    identity: str,
    operation: str,
) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)
    creds = creds_path(generated, identity)

    async def _run() -> None:
        nc = await connect_nats(nats_url=nats_url, creds_file=creds, ca_file=ca)
        try:
            js = nc.jetstream()
            if operation == "bridge_forbidden_subject":
                with pytest.raises(Exception) as exc:
                    await js.publish("hudhud.shipment.shipment.fact.created.v1", b"x", timeout=1)
                assert_acl_denied(exc.value)
            elif operation == "bridge_create_stream":
                with pytest.raises(Exception) as exc:
                    await js.add_stream(StreamConfig(name="FORBIDDEN", subjects=["forbidden.>"]))
                assert_acl_denied(exc.value)
            elif operation == "audit_publish_business":
                with pytest.raises(Exception) as exc:
                    await js.publish(A2_SUBJECT, b"forbidden", timeout=1)
                assert_acl_denied(exc.value)
            elif operation == "audit_access_tracking_durable":
                with pytest.raises(Exception) as exc:
                    await js.consumer_info(SHIPMENT_STREAM, TRACKING_DURABLE)
                assert_acl_denied(exc.value)
            elif operation == "audit_create_consumer":
                with pytest.raises(Exception) as exc:
                    await js.add_consumer(
                        AUDIT_STREAM,
                        ConsumerConfig(
                            durable_name="forbidden_consumer",
                            filter_subject=A2_SUBJECT,
                        ),
                    )
                assert_acl_denied(exc.value)
            elif operation == "tracking_access_audit_durable":
                with pytest.raises(Exception) as exc:
                    await js.consumer_info(AUDIT_STREAM, AUDIT_DURABLE)
                assert_acl_denied(exc.value)
            elif operation == "tracking_publish_business":
                with pytest.raises(Exception) as exc:
                    await js.publish(A1_SUBJECT, b"forbidden", timeout=1)
                assert_acl_denied(exc.value)
            elif operation == "bootstrap_publish_a2":
                with pytest.raises(Exception) as exc:
                    await js.publish(A2_SUBJECT, b"forbidden")
                assert_acl_denied(exc.value)
            elif operation == "audit_ungranted_js_api":
                with pytest.raises(Exception) as exc:
                    await js.stream_info(SHIPMENT_STREAM)
                assert_acl_denied(exc.value)
            else:
                msg = f"unknown operation {operation}"
                raise AssertionError(msg)
        finally:
            await nc.drain()

    asyncio.run(_run())


def test_tls_trusted_ca_succeeds(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)

    async def _run() -> None:
        nc = await connect_nats(
            nats_url=nats_url,
            creds_file=creds_path(generated, IDENTITY_BRIDGE_V1),
            ca_file=ca,
        )
        await nc.flush()
        await nc.drain()

    asyncio.run(_run())


def test_tls_untrusted_ca_fails(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)
    wrong_ca = generated / "ca" / "wrong-ca.pem"
    wrong_ca.write_text("-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----\n")

    async def _run() -> None:
        with pytest.raises(Exception) as exc:
            await connect_nats(
                nats_url=nats_url,
                creds_file=creds_path(generated, IDENTITY_BRIDGE_V1),
                ca_file=wrong_ca,
            )
        assert is_tls_error(exc.value)
        assert "seed" not in sanitize_error_message(str(exc.value)).lower()

    asyncio.run(_run())


def test_tls_hostname_mismatch_fails(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)

    async def _run() -> None:
        context = ssl.create_default_context(cafile=str(ca))
        with pytest.raises(Exception) as exc:
            await asyncio.wait_for(
                nats.connect(
                    servers=[nats_url],
                    user_credentials=str(creds_path(generated, IDENTITY_BRIDGE_V1)),
                    tls=context,
                    tls_hostname="wrong-hostname.example",
                    connect_timeout=CONNECT_TIMEOUT_SECONDS,
                ),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        assert is_tls_error(exc.value)

    asyncio.run(_run())


def test_tls_plaintext_fails(nats_security_stack: dict) -> None:
    _, ca, generated = _stack_urls(nats_security_stack)
    plaintext_url = f"nats://127.0.0.1:{discover_host_port(NATS_SERVICE, 4222)}"

    async def _run() -> None:
        with pytest.raises(Exception) as exc:
            await connect_nats(
                nats_url=plaintext_url,
                creds_file=creds_path(generated, IDENTITY_BRIDGE_V1),
                ca_file=ca,
                allow_plaintext=True,
            )
        message = sanitize_error_message(str(exc.value)).lower()
        assert is_tls_error(exc.value) or "connection" in message or "certificate" in message

    asyncio.run(_run())


def test_rotation_bridge_v1_v2_overlap_and_revocation(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)

    for identity in (IDENTITY_BRIDGE_V1, IDENTITY_BRIDGE_V2):
        result = run_bridge_publish_probe(
            nats_url=nats_url,
            ca_file=ca,
            creds_file=creds_path(generated, identity),
            subject=A2_SUBJECT,
            payload='{"rotation":"overlap"}',
            msg_id=f"rotation-{uuid.uuid4()}",
        )
        assert result["puback_received"] is True

    revoke_user(IDENTITY_BRIDGE_V1)
    wait_for_resolver_propagation()
    nats_url = _refresh_nats_url()

    revoked = run_bridge_publish_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_BRIDGE_V1),
        subject=A2_SUBJECT,
        payload='{"rotation":"revoked"}',
        msg_id=f"rotation-revoked-{uuid.uuid4()}",
    )
    assert revoked["puback_received"] is False

    active = run_bridge_publish_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_BRIDGE_V2),
        subject=A2_SUBJECT,
        payload='{"rotation":"v2-active"}',
        msg_id=f"rotation-v2-{uuid.uuid4()}",
    )
    assert active["puback_received"] is True


def test_rotation_audit_v1_v2_overlap_and_revocation(nats_security_stack: dict) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)

    for identity in (IDENTITY_AUDIT_V1, IDENTITY_AUDIT_V2):
        result = run_audit_readiness_probe(
            nats_url=nats_url,
            ca_file=ca,
            creds_file=creds_path(generated, identity),
        )
        assert result["binding_verified"] is True

    revoke_user(IDENTITY_AUDIT_V1)
    wait_for_resolver_propagation()
    nats_url = _refresh_nats_url()

    revoked = run_audit_readiness_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_AUDIT_V1),
    )
    assert revoked.get("binding_verified") is not True

    active = run_audit_readiness_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_AUDIT_V2),
    )
    assert active["binding_verified"] is True


def test_revocation_audit_v1_blocks_while_bridge_and_topology_remain(
    nats_security_stack: dict,
) -> None:
    nats_url, ca, generated = _stack_urls(nats_security_stack)

    active_v1 = run_audit_readiness_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_AUDIT_V1),
    )
    if active_v1.get("binding_verified") is True:
        revoke_user(IDENTITY_AUDIT_V1)
        wait_for_resolver_propagation()
        nats_url = _refresh_nats_url()

    revoked = run_audit_readiness_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_AUDIT_V1),
    )
    assert revoked.get("binding_verified") is not True

    active = run_audit_readiness_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_AUDIT_V2),
    )
    assert active["binding_verified"] is True

    bridge = run_bridge_publish_probe(
        nats_url=nats_url,
        ca_file=ca,
        creds_file=creds_path(generated, IDENTITY_BRIDGE_V2),
        subject=A2_SUBJECT,
        payload='{"revocation":"bridge-unaffected"}',
        msg_id=f"revocation-bridge-{uuid.uuid4()}",
    )
    assert bridge["puback_received"] is True

    refreshed = extract_generated_material()
    try:
        assert "operator.jwt" in {path.name for path in (refreshed / "jwt").iterdir()}
        assert (refreshed / "creds" / f"{IDENTITY_AUDIT_V1}.creds").exists()
    finally:
        shutil.rmtree(refreshed, ignore_errors=True)

    async def _topology_intact() -> None:
        nc = await connect_nats(
            nats_url=nats_url,
            creds_file=creds_path(generated, IDENTITY_BOOTSTRAP),
            ca_file=ca,
        )
        try:
            js = nc.jetstream()
            await js.stream_info(AUDIT_STREAM)
            await js.consumer_info(AUDIT_STREAM, AUDIT_DURABLE)
        finally:
            await nc.drain()

    asyncio.run(_topology_intact())
