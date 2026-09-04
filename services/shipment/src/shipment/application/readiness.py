"""Readiness evaluation for Shipment HTTP and consumer runtime gates."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from shipment.config import (
    AcceptanceIngestionMode,
    PersistenceBackend,
    RuntimeEnvironment,
    ShipmentSettings,
)
from shipment.infrastructure.persistence.session import ping_database


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, bool] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()


def evaluate_readiness(
    *,
    settings: ShipmentSettings,
    engine: Engine | None,
    persistence_wired: bool,
    authorization_adapter_ready: bool,
    nats_reachable: bool = False,
    nats_binding_verified: bool = False,
    migrations_applied: bool | None = None,
) -> ReadinessReport:
    database_configured = bool(settings.database_url)
    database_reachable = engine is not None and ping_database(engine)
    production_gates = settings.environment is not RuntimeEnvironment.PRODUCTION or (
        bool(settings.database_url)
        and settings.adr_0010_credentials_configured
        and settings.persistence_backend is not PersistenceBackend.MEMORY
    )
    auth_required = settings.environment in {
        RuntimeEnvironment.STAGING,
        RuntimeEnvironment.PRODUCTION,
    }
    memory_in_production = (
        settings.environment is RuntimeEnvironment.PRODUCTION
        and settings.persistence_backend is PersistenceBackend.MEMORY
    )
    nats_configured = not settings.nats_enabled or bool(settings.nats_url)
    nats_tls_ready = (
        not settings.nats_enabled
        or settings.environment is not RuntimeEnvironment.PRODUCTION
        or settings.nats_tls_enabled
    )
    nats_credentials_ready = (
        not settings.nats_enabled
        or settings.environment is not RuntimeEnvironment.PRODUCTION
        or settings.adr_0010_credentials_configured
    )
    nats_ready = not settings.nats_enabled or (
        nats_reachable
        and nats_binding_verified
        and nats_tls_ready
        and nats_credentials_ready
    )

    mode = settings.acceptance_ingestion_mode
    mode_configured = mode is not None
    mode_disabled = mode is AcceptanceIngestionMode.DISABLED
    http_enabled = settings.http_acceptance_enabled()
    native_enabled = settings.native_pickup_fact_enabled()
    # Closed enum guarantees mutual exclusion; surface as an explicit check.
    paths_mutually_exclusive = not (http_enabled and native_enabled)

    native_durable_ok = (
        not native_enabled or settings.exact_durable_binding_configured()
    )
    native_db_ok = not native_enabled or (
        bool(settings.database_url)
        and settings.persistence_backend is PersistenceBackend.POSTGRES
    )
    if migrations_applied is None:
        native_migrations_ok = not native_enabled or (
            settings.environment is RuntimeEnvironment.TEST or database_reachable
        )
    else:
        native_migrations_ok = not native_enabled or migrations_applied

    staging_or_prod = settings.environment in {
        RuntimeEnvironment.STAGING,
        RuntimeEnvironment.PRODUCTION,
    }
    native_adr_ok = not native_enabled or not staging_or_prod or (
        settings.adr_0010_credentials_configured and settings.nats_tls_enabled
    )
    native_cutover_ok = (
        not native_enabled
        or not staging_or_prod
        or settings.acceptance_cutover_evidence_confirmed
    )
    native_revocation_ok = (
        not native_enabled
        or not staging_or_prod
        or settings.legacy_pickup_acceptance_writer_revocation_externally_confirmed
    )
    native_nats_binding_ok = not native_enabled or nats_binding_verified

    test_env = settings.environment is RuntimeEnvironment.TEST
    checks = {
        "configuration": database_configured or test_env,
        "database_configured": database_configured or test_env,
        "postgres_adapter_present": persistence_wired,
        "database_reachable": (
            True if settings.environment is RuntimeEnvironment.TEST else database_reachable
        ),
        "authorization_adapter_ready": (
            True if not auth_required else authorization_adapter_ready
        ),
        "production_gates": production_gates,
        "nats_configured": nats_configured,
        "nats_reachable": nats_reachable if settings.nats_enabled else True,
        "nats_binding_verified": nats_binding_verified if settings.nats_enabled else True,
        "nats_ready": nats_ready,
        "nats_tls_ready": nats_tls_ready,
        "nats_credentials_ready": nats_credentials_ready,
        "memory_persistence_allowed": not memory_in_production,
        "acceptance_ingestion_mode_configured": mode_configured,
        "acceptance_ingestion_enabled": mode_configured and not mode_disabled,
        "http_acceptance_enabled": http_enabled,
        "native_pickup_fact_enabled": native_enabled,
        "acceptance_paths_mutually_exclusive": paths_mutually_exclusive,
        "native_durable_binding_ok": native_durable_ok,
        "native_database_ok": native_db_ok,
        "native_migrations_ok": native_migrations_ok,
        "native_adr_0010_ok": native_adr_ok,
        "native_cutover_evidence_ok": native_cutover_ok,
        "native_legacy_revocation_externally_confirmed": native_revocation_ok,
        "native_nats_binding_ok": native_nats_binding_ok,
        "production_ready_false": True,
    }
    blockers: list[str] = []
    if not checks["configuration"]:
        blockers.append("configuration_incomplete")
    if not checks["postgres_adapter_present"]:
        blockers.append("postgres_adapter_not_wired")
    if settings.environment is not RuntimeEnvironment.TEST and not checks["database_reachable"]:
        blockers.append("database_unreachable")
    if auth_required and not authorization_adapter_ready:
        blockers.append("authorization_adapter_not_ready")
    if not checks["production_gates"]:
        blockers.append("production_gates_unset")
    if settings.nats_enabled and not settings.nats_url:
        blockers.append("nats_url_missing")
    if settings.nats_enabled and not nats_reachable:
        blockers.append("nats_unreachable")
    if settings.nats_enabled and not nats_binding_verified:
        blockers.append("nats_binding_unverified")
    if settings.nats_enabled and not nats_tls_ready:
        blockers.append("nats_tls_required_in_production")
    if settings.nats_enabled and not nats_credentials_ready:
        blockers.append("nats_credentials_required_in_production")
    if memory_in_production:
        blockers.append("memory_persistence_forbidden_in_production")

    if not mode_configured:
        blockers.append("acceptance_ingestion_mode_missing")
    elif mode_disabled:
        blockers.append("acceptance_ingestion_disabled")
    if not paths_mutually_exclusive:
        blockers.append("compatibility_native_mode_conflict")
    if native_enabled and not native_durable_ok:
        blockers.append("native_durable_binding_missing")
    if native_enabled and not native_db_ok:
        blockers.append("native_database_unavailable")
    if native_enabled and not native_migrations_ok:
        blockers.append("native_migrations_unavailable")
    if native_enabled and not native_adr_ok:
        blockers.append("adr_0010_credential_tls_gate_missing")
    if native_enabled and not native_cutover_ok:
        blockers.append("cutover_evidence_gate_missing")
    if native_enabled and not native_revocation_ok:
        blockers.append("legacy_writer_revocation_not_externally_confirmed")
    if native_enabled and not native_nats_binding_ok:
        blockers.append("native_durable_binding_unverified")

    ready = (
        checks["configuration"]
        and checks["postgres_adapter_present"]
        and checks["database_reachable"]
        and checks["authorization_adapter_ready"]
        and checks["production_gates"]
        and checks["nats_configured"]
        and checks["nats_ready"]
        and checks["memory_persistence_allowed"]
        and checks["acceptance_ingestion_mode_configured"]
        and checks["acceptance_ingestion_enabled"]
        and checks["acceptance_paths_mutually_exclusive"]
        and checks["native_durable_binding_ok"]
        and checks["native_database_ok"]
        and checks["native_migrations_ok"]
        and checks["native_adr_0010_ok"]
        and checks["native_cutover_evidence_ok"]
        and checks["native_legacy_revocation_externally_confirmed"]
        and checks["native_nats_binding_ok"]
        and checks["production_ready_false"]
    )
    return ReadinessReport(ready=ready, checks=checks, blockers=tuple(blockers))
