"""Static proof that Pickup/Shipment cannot disable NATS certificate verification."""

from __future__ import annotations

from .helpers import REPO_ROOT

PICKUP_CLIENT = (
    REPO_ROOT / "services" / "pickup" / "src" / "pickup" / "infrastructure" / "nats" / "client.py"
)
PICKUP_CONFIG = REPO_ROOT / "services" / "pickup" / "src" / "pickup" / "config.py"
SHIPMENT_CONNECTION = (
    REPO_ROOT
    / "services"
    / "shipment"
    / "src"
    / "shipment"
    / "infrastructure"
    / "jetstream"
    / "connection.py"
)
SHIPMENT_CONFIG = REPO_ROOT / "services" / "shipment" / "src" / "shipment" / "config.py"

FORBIDDEN_TLS_DISABLE_MARKERS = (
    "CERT_NONE",
    "CERT_OPTIONAL",
    "check_hostname = False",
    "check_hostname=False",
    "nats_tls_insecure",
    "skip_verify",
    "verify=False",
    "ssl._create_unverified_context",
    "PROTOCOL_TLS_SERVER",
)


def _assert_no_tls_disablement(path) -> str:
    text = path.read_text(encoding="utf-8")
    for marker in FORBIDDEN_TLS_DISABLE_MARKERS:
        assert marker not in text, f"{path.name} contains TLS disablement marker {marker}"
    return text


def test_pickup_tls_verification_cannot_be_disabled() -> None:
    client = _assert_no_tls_disablement(PICKUP_CLIENT)
    config = _assert_no_tls_disablement(PICKUP_CONFIG)
    assert "ssl.create_default_context" in client
    assert "staging/production requires explicit NATS TLS" in client
    assert "nats_tls_enabled" in config
    assert "STAGING" in config
    assert "PRODUCTION" in config


def test_shipment_tls_verification_cannot_be_disabled() -> None:
    connection = _assert_no_tls_disablement(SHIPMENT_CONNECTION)
    config = _assert_no_tls_disablement(SHIPMENT_CONFIG)
    assert "ssl.create_default_context" in connection
    assert "Staging/production NATS requires TLS" in connection
    assert "nats_tls_enabled" in config
    assert "STAGING" in config
    assert "PRODUCTION" in config
