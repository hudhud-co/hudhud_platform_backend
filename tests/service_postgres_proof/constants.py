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
BRIDGE_ROLE = "bridge_svc"
BRIDGE_ROLE_PASSWORD = "bridge_svc_dev_only"
AUDIT_ROLE = "audit_svc"
AUDIT_ROLE_PASSWORD = "audit_svc_dev_only"

BRIDGE_EXPECTED_HEAD = "w5a_bridge_pipeline_002"
AUDIT_EXPECTED_HEAD = "w5b_audit_observation_001"

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
ALLOWED_DATABASES = frozenset({OWNER_DATABASE, BRIDGE_DATABASE, AUDIT_DATABASE})
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
