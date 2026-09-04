"""Shared constants for the service PostgreSQL proof lab."""

from __future__ import annotations

COMPOSE_PROJECT = "hudhud-service-postgres-proof-lab"
COMPOSE_PROFILE = "service-postgres-proof"
NETWORK_NAME = "hudhud_service_postgres_proof"
VOLUME_NAME = "hudhud_service_postgres_proof_pgdata"
POSTGRES_SERVICE = "postgres"

OWNER_USER = "svc_pg_lab_owner"
OWNER_PASSWORD = "svc_pg_lab_owner_dev_only"
OWNER_DATABASE = "svc_pg_lab"

BRIDGE_DATABASE = "bridge_db"
AUDIT_DATABASE = "audit_db"
SHIPMENT_DATABASE = "shipment_db"
PICKUP_DATABASE = "pickup_db"

BRIDGE_ROLE = "bridge_svc"
AUDIT_ROLE = "audit_svc"
SHIPMENT_ROLE = "shipment_svc"
PICKUP_ROLE = "pickup_svc"

BRIDGE_ROLE_PASSWORD = "bridge_svc_dev_only"
AUDIT_ROLE_PASSWORD = "audit_svc_dev_only"
SHIPMENT_ROLE_PASSWORD = "shipment_svc_dev_only"
PICKUP_ROLE_PASSWORD = "pickup_svc_dev_only"

BRIDGE_EXPECTED_HEAD = "w5a_bridge_pipeline_002"
AUDIT_EXPECTED_HEAD = "w5b_audit_observation_001"
SHIPMENT_EXPECTED_HEAD = "w17f_accepted_inbox_001"
PICKUP_EXPECTED_HEAD = "w17e_pickup_accepted_outbox_001"

SHIPMENT_PRE_CUSTODY_REVISION = "w16a_acceptance_idempotency_001"

LAB_DATABASES = frozenset(
    {
        BRIDGE_DATABASE,
        AUDIT_DATABASE,
        SHIPMENT_DATABASE,
        PICKUP_DATABASE,
    }
)

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
ALLOWED_DATABASES = frozenset({OWNER_DATABASE, *LAB_DATABASES})
FORBIDDEN_URL_FRAGMENTS = (
    "prod",
    "staging",
    "legacy",
    "amazonaws.com",
    "rds.",
    "hudhud-backend",
)

BRIDGE_TABLES = frozenset(
    {
        "bridge_landing",
        "bridge_checkpoint",
        "bridge_integration_outbox",
    }
)

AUDIT_TABLES = frozenset(
    {
        "audit_integration_inbox",
        "legacy_audit_observations",
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

PICKUP_TABLES = frozenset(
    {
        "pickup_tasks",
        "pickup_recovery_history",
        "pickup_recovery_idempotency",
        "pickup_acceptance_idempotency",
        "pickup_integration_outbox",
    }
)
