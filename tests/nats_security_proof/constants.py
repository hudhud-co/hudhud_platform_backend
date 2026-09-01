"""Shared constants for the NATS security proof lab."""

from __future__ import annotations

COMPOSE_PROJECT = "hudhud-nats-security-proof-lab"
COMPOSE_PROFILE = "nats-security-proof"
NETWORK_NAME = "hudhud_nats_security_proof"
VOLUME_JS_NAME = "hudhud_nats_security_proof_jetstream"
VOLUME_GENERATED_NAME = "hudhud_nats_security_proof_generated"

NATS_SERVICE = "nats"
NATS_CONTAINER = "hudhud-nats-security-proof-nats"
SECURITY_INIT_SERVICE = "security-init"
TOPOLOGY_BOOTSTRAP_SERVICE = "topology-bootstrap"

NATS_IMAGE = "nats:2.10.24-alpine"
SECURITY_INIT_IMAGE = "hudhud-nats-security-proof-security-init:0.14.3"

TLS_SERVER_NAME = "hudhud-nats-security-proof"

SHIPMENT_STREAM = "HUDHUD_SHIPMENT"
AUDIT_STREAM = "HUDHUD_AUDIT"
TRACKING_DURABLE = "tracking_bridge_timeline_v1"
AUDIT_DURABLE = "audit_bridge_entry_v1"

A1_SUBJECT = "hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1"
A2_SUBJECT = "hudhud.audit.legacy_bridge.observation.audit_entry.v1"

IDENTITY_BOOTSTRAP = "hudhud-eventing-bootstrap"
IDENTITY_BRIDGE_V1 = "legacy-event-bridge-v1"
IDENTITY_BRIDGE_V2 = "legacy-event-bridge-v2"
IDENTITY_AUDIT_V1 = "audit-v1"
IDENTITY_AUDIT_V2 = "audit-v2"
IDENTITY_TRACKING_V1 = "tracking-v1"
IDENTITY_TRACKING_V2 = "tracking-v2"
IDENTITY_BREAK_GLASS = "hudhud-nats-break-glass"

CONNECT_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 5.0
FETCH_TIMEOUT_SECONDS = 3.0
INTEGRATION_OPERATION_TIMEOUT_SECONDS = 10.0
RUNTIME_SUBPROCESS_TIMEOUT_SECONDS = 120.0

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
FORBIDDEN_URL_FRAGMENTS = (
    "prod",
    "staging",
    "legacy",
    "amazonaws.com",
    "rds.",
    "hudhud-backend",
)

SECRET_FILE_SUFFIXES = (".creds", ".nk", ".pem", ".key", ".jwt", ".csr")
