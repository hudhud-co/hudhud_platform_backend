"""Readiness evaluation for Pickup recovery HTTP runtime gates."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from pickup.config import PersistenceBackend, PickupSettings, RuntimeEnvironment
from pickup.infrastructure.persistence.session import ping_database


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, bool] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()


def evaluate_readiness(
    *,
    settings: PickupSettings,
    engine: Engine | None,
    persistence_wired: bool,
    authorization_configured: bool,
    shipment_eligibility_configured: bool,
    nats_reachable: bool | None = None,
) -> ReadinessReport:
    database_configured = bool(settings.database_url)
    database_reachable = engine is not None and ping_database(engine)
    memory_in_production = (
        settings.environment is RuntimeEnvironment.PRODUCTION
        and settings.persistence_backend is PersistenceBackend.MEMORY
    )
    skip_live_db = settings.environment is RuntimeEnvironment.TEST
    relay_active = settings.relay_enabled
    nats_check = True
    if relay_active:
        nats_check = nats_reachable is True

    production_gates_ok = True
    if settings.environment is RuntimeEnvironment.PRODUCTION:
        production_gates_ok = not settings.production_ready and (
            not relay_active or settings.adr_0010_credentials_configured
        )

    checks = {
        "production_gates": production_gates_ok,
        "postgres_adapter_present": persistence_wired,
        "database_configured": database_configured or skip_live_db,
        "database_reachable": database_reachable or skip_live_db,
        "authorization_configured": authorization_configured,
        "shipment_eligibility_configured": shipment_eligibility_configured,
        "memory_persistence_allowed": not memory_in_production,
        "relay_configuration_valid": settings.relay_configuration_valid(),
        "nats_reachable": nats_check,
        "nats_publisher_active": relay_active,
        "production_ready_false": not settings.production_ready,
    }
    blockers: list[str] = []
    if not checks["production_gates"]:
        blockers.append("production_gates_unset")
    if not checks["postgres_adapter_present"]:
        blockers.append("postgres_adapter_not_wired")
    if not skip_live_db and not database_configured:
        blockers.append("database_url_missing")
    if not skip_live_db and not checks["database_reachable"]:
        blockers.append("database_unreachable")
    if memory_in_production:
        blockers.append("memory_persistence_forbidden_in_production")
    if not checks["authorization_configured"]:
        blockers.append("authorization_adapter_not_configured")
    if not checks["shipment_eligibility_configured"]:
        blockers.append("shipment_eligibility_adapter_deferred")
    if relay_active and not checks["relay_configuration_valid"]:
        blockers.append("relay_configuration_invalid")
    if relay_active and not checks["nats_reachable"]:
        blockers.append("nats_unreachable")
    if not relay_active:
        blockers.append("nats_publisher_not_enabled")
    if settings.production_ready:
        blockers.append("production_ready_claimed")

    ready = (
        checks["production_gates"]
        and checks["postgres_adapter_present"]
        and checks["database_configured"]
        and checks["database_reachable"]
        and checks["authorization_configured"]
        and checks["shipment_eligibility_configured"]
        and checks["memory_persistence_allowed"]
        and checks["relay_configuration_valid"]
        and checks["production_ready_false"]
        and (not relay_active or checks["nats_reachable"])
    )
    return ReadinessReport(ready=ready, checks=checks, blockers=tuple(blockers))
