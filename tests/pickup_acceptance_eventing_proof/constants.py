"""Shared constants for the Pickup acceptance eventing proof lab."""

from __future__ import annotations

COMPOSE_PROJECT = "hudhud-pickup-acceptance-eventing-proof-lab"
COMPOSE_PROFILE = "pickup-acceptance-eventing-proof"
NETWORK_NAME = "hudhud_pickup_acceptance_eventing_proof"
VOLUME_PG_NAME = "hudhud_pickup_acceptance_eventing_proof_pgdata"
VOLUME_JS_NAME = "hudhud_pickup_acceptance_eventing_proof_jetstream"

POSTGRES_SERVICE = "postgres"
NATS_SERVICE = "nats"

OWNER_USER = "pickup_acc_lab_owner"
OWNER_PASSWORD = "pickup_acc_lab_owner_dev_only"
OWNER_DATABASE = "pickup_acc_lab"

PICKUP_DATABASE = "pickup_db"
SHIPMENT_DATABASE = "shipment_db"
PICKUP_ROLE = "pickup_svc"
PICKUP_ROLE_PASSWORD = "pickup_svc_dev_only"
SHIPMENT_ROLE = "shipment_svc"
SHIPMENT_ROLE_PASSWORD = "shipment_svc_dev_only"

PICKUP_EXPECTED_HEAD = "w17e_pickup_accepted_outbox_001"
SHIPMENT_EXPECTED_HEAD = "w17f_accepted_inbox_001"

PICKUP_STREAM = "HUDHUD_PICKUP"
PICKUP_DURABLE = "shipment_pickup_facts_v1"
PICKUP_SUBJECT = "hudhud.pickup.pickup.fact.accepted.v1"
ACK_POLICY = "explicit"

PULL_BATCH_SIZE = 1
HANDLER_CONCURRENCY = 1
PULL_FETCH_TIMEOUT_SECONDS = 2.0
INTEGRATION_OPERATION_TIMEOUT_SECONDS = 20.0
RUNTIME_HARD_TIMEOUT_SECONDS = 120

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
ALLOWED_DATABASES = frozenset({OWNER_DATABASE, PICKUP_DATABASE, SHIPMENT_DATABASE})
FORBIDDEN_URL_FRAGMENTS = (
    "prod",
    "staging",
    "legacy",
    "amazonaws.com",
    "rds.",
    "hudhud-backend",
)

PICKUP_TABLES = frozenset(
    {
        "pickup_tasks",
        "pickup_recovery_history",
        "pickup_recovery_idempotency",
        "pickup_acceptance_idempotency",
        "pickup_integration_outbox",
    }
)

SHIPMENT_TABLES = frozenset(
    {
        "order_intents",
        "shipments",
        "pickup_task_snapshots",
        "shipment_events",
        "acceptance_audit_logs",
        "acceptance_decisions",
        "acceptance_idempotency",
        "shipment_integration_inbox",
    }
)
