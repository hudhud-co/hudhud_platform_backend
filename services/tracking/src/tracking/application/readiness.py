"""Readiness evaluation for Tracking runtime gates."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from tracking.config import PersistenceBackend, RuntimeEnvironment, TrackingSettings
from tracking.infrastructure.persistence.session import ping_database


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, bool] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()


def evaluate_readiness(
    *,
    settings: TrackingSettings,
    engine: Engine | None,
    persistence_wired: bool,
    query_authorizer_configured: bool = False,
    jwt_verifier_configured: bool = False,
    jwks_dependency_available: bool = False,
    shipment_access_policy_configured: bool = False,
    query_persistence_configured: bool = False,
    nats_reachable: bool = False,
    nats_binding_verified: bool = False,
) -> ReadinessReport:
    production_gates = settings.environment is not RuntimeEnvironment.PRODUCTION or (
        settings.adr_0010_credentials_configured
        and settings.persistence_backend is not PersistenceBackend.MEMORY
    )
    postgres_adapter_present = persistence_wired
    database_configured = bool(settings.database_url)
    database_reachable = engine is not None and ping_database(engine)
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
    nats_ready = not settings.nats_enabled or (
        nats_reachable and nats_binding_verified and nats_tls_ready
    )

    query_gates_required = settings.environment in {
        RuntimeEnvironment.PRODUCTION,
        RuntimeEnvironment.STAGING,
    }

    checks = {
        "production_gates": production_gates,
        "postgres_adapter_present": postgres_adapter_present,
        "database_configured": database_configured,
        "database_reachable": database_reachable,
        "query_authorizer_configured": query_authorizer_configured,
        "jwt_verifier_configured": jwt_verifier_configured,
        "jwks_dependency_available": jwks_dependency_available,
        "shipment_access_policy_configured": shipment_access_policy_configured,
        "query_persistence_configured": query_persistence_configured,
        "nats_configured": nats_configured,
        "nats_reachable": nats_reachable if settings.nats_enabled else True,
        "nats_binding_verified": nats_binding_verified if settings.nats_enabled else True,
        "nats_ready": nats_ready,
        "nats_tls_ready": nats_tls_ready,
        "memory_persistence_allowed": not memory_in_production,
    }
    blockers: list[str] = []
    if not checks["production_gates"]:
        blockers.append("production_gates_unset")
    if not checks["postgres_adapter_present"]:
        blockers.append("postgres_adapter_not_wired")
    if settings.environment.value != "test" and not checks["database_reachable"]:
        blockers.append("database_unreachable")
    if settings.nats_enabled and not settings.nats_url:
        blockers.append("nats_url_missing")
    if settings.nats_enabled and not nats_reachable:
        blockers.append("nats_unreachable")
    if settings.nats_enabled and not nats_binding_verified:
        blockers.append("nats_binding_unverified")
    if settings.nats_enabled and not nats_tls_ready:
        blockers.append("nats_tls_required_in_production")
    if memory_in_production:
        blockers.append("memory_persistence_forbidden_in_production")
    if query_gates_required and not checks["query_authorizer_configured"]:
        blockers.append("query_authorizer_not_configured")
    if query_gates_required and not checks["jwt_verifier_configured"]:
        blockers.append("jwt_verifier_not_configured")
    if query_gates_required and not checks["jwks_dependency_available"]:
        blockers.append("jwks_dependency_unavailable")
    if query_gates_required and not checks["shipment_access_policy_configured"]:
        blockers.append("shipment_access_policy_not_configured")
    if query_gates_required and not checks["query_persistence_configured"]:
        blockers.append("query_persistence_not_configured")

    ready = (
        checks["production_gates"]
        and checks["postgres_adapter_present"]
        and (settings.environment.value == "test" or checks["database_reachable"])
        and checks["nats_configured"]
        and checks["nats_ready"]
        and checks["memory_persistence_allowed"]
        and (not query_gates_required or checks["query_authorizer_configured"])
        and (not query_gates_required or checks["jwt_verifier_configured"])
        and (not query_gates_required or checks["jwks_dependency_available"])
        and (not query_gates_required or checks["shipment_access_policy_configured"])
        and (not query_gates_required or checks["query_persistence_configured"])
    )
    return ReadinessReport(ready=ready, checks=checks, blockers=tuple(blockers))
