"""Shared constants for the observation eventing proof lab."""

from __future__ import annotations

COMPOSE_PROJECT = "hudhud-observation-eventing-proof-lab"
COMPOSE_PROFILE = "observation-eventing-proof"
NETWORK_NAME = "hudhud_observation_eventing_proof"
VOLUME_PG_NAME = "hudhud_observation_eventing_proof_pgdata"
VOLUME_JS_NAME = "hudhud_observation_eventing_proof_jetstream"

POSTGRES_SERVICE = "postgres"
NATS_SERVICE = "nats"

OWNER_USER = "obs_evt_lab_owner"
OWNER_PASSWORD = "obs_evt_lab_owner_dev_only"
OWNER_DATABASE = "obs_evt_lab"

BRIDGE_DATABASE = "bridge_db"
AUDIT_DATABASE = "audit_db"
BRIDGE_ROLE = "bridge_svc"
BRIDGE_ROLE_PASSWORD = "bridge_svc_dev_only"
AUDIT_ROLE = "audit_svc"
AUDIT_ROLE_PASSWORD = "audit_svc_dev_only"

BRIDGE_EXPECTED_HEAD = "w5a_bridge_pipeline_002"
AUDIT_EXPECTED_HEAD = "w5b_audit_observation_001"

A2_STREAM = "HUDHUD_AUDIT"
A2_DURABLE = "audit_bridge_entry_v1"
A2_SUBJECT = "hudhud.audit.legacy_bridge.observation.audit_entry.v1"

PULL_BATCH_SIZE = 2
HANDLER_CONCURRENCY = 1
PULL_FETCH_TIMEOUT_SECONDS = 2.0
INTEGRATION_OPERATION_TIMEOUT_SECONDS = 15.0

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
