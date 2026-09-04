"""Readiness evaluation for Shipment HTTP runtime gates."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from shipment.config import RuntimeEnvironment, ShipmentSettings
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
) -> ReadinessReport:
    database_configured = bool(settings.database_url)
    database_reachable = engine is not None and ping_database(engine)
    production_gates = settings.environment is not RuntimeEnvironment.PRODUCTION or bool(
        settings.database_url
    )
    auth_required = settings.environment in {
        RuntimeEnvironment.STAGING,
        RuntimeEnvironment.PRODUCTION,
    }

    test_env = settings.environment is RuntimeEnvironment.TEST
    checks = {
        "configuration": database_configured or test_env,
        "database_configured": database_configured or test_env,
        "postgres_adapter_present": persistence_wired,
        "database_reachable": (
            True
            if settings.environment is RuntimeEnvironment.TEST
            else database_reachable
        ),
        "authorization_adapter_ready": (
            True if not auth_required else authorization_adapter_ready
        ),
        "production_gates": production_gates,
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

    ready = (
        checks["configuration"]
        and checks["postgres_adapter_present"]
        and checks["database_reachable"]
        and checks["authorization_adapter_ready"]
        and checks["production_gates"]
    )
    return ReadinessReport(ready=ready, checks=checks, blockers=tuple(blockers))
