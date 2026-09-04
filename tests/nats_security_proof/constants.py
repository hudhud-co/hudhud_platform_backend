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
PICKUP_STREAM = "HUDHUD_PICKUP"
TRACKING_DURABLE = "tracking_bridge_timeline_v1"
AUDIT_DURABLE = "audit_bridge_entry_v1"
SHIPMENT_PICKUP_DURABLE = "shipment_pickup_facts_v1"

A1_SUBJECT = "hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1"
A2_SUBJECT = "hudhud.audit.legacy_bridge.observation.audit_entry.v1"
PICKUP_ACCEPTED_SUBJECT = "hudhud.pickup.pickup.fact.accepted.v1"
PICKUP_FORBIDDEN_SUBJECT = "hudhud.pickup.pickup.fact.handover_completed.v1"

IDENTITY_BOOTSTRAP = "hudhud-eventing-bootstrap"
IDENTITY_BRIDGE_V1 = "legacy-event-bridge-v1"
IDENTITY_BRIDGE_V2 = "legacy-event-bridge-v2"
IDENTITY_AUDIT_V1 = "audit-v1"
IDENTITY_AUDIT_V2 = "audit-v2"
IDENTITY_TRACKING_V1 = "tracking-v1"
IDENTITY_TRACKING_V2 = "tracking-v2"
IDENTITY_PICKUP_V1 = "pickup-v1"
IDENTITY_PICKUP_V2 = "pickup-v2"
IDENTITY_SHIPMENT_V1 = "shipment-v1"
IDENTITY_SHIPMENT_V2 = "shipment-v2"
IDENTITY_BREAK_GLASS = "hudhud-nats-break-glass"

PICKUP_JS_INFO = f"$JS.API.CONSUMER.INFO.{PICKUP_STREAM}.{SHIPMENT_PICKUP_DURABLE}"
PICKUP_JS_NEXT = f"$JS.API.CONSUMER.MSG.NEXT.{PICKUP_STREAM}.{SHIPMENT_PICKUP_DURABLE}"
PICKUP_JS_ACK = f"$JS.ACK.{PICKUP_STREAM}.{SHIPMENT_PICKUP_DURABLE}.>"
PICKUP_JS_FC = f"$JS.FC.{PICKUP_STREAM}.>"

CONNECT_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 5.0
FETCH_TIMEOUT_SECONDS = 3.0
INTEGRATION_OPERATION_TIMEOUT_SECONDS = 10.0
RUNTIME_SUBPROCESS_TIMEOUT_SECONDS = 180.0

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
