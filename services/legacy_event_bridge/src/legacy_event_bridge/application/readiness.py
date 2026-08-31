"""Readiness evaluation for Bridge runtime gates."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from legacy_event_bridge.config import BridgeSettings
from legacy_event_bridge.infrastructure.persistence.session import ping_database


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, bool] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()


def evaluate_readiness(
    *,
    settings: BridgeSettings,
    engine: Engine | None,
    persistence_wired: bool,
    nats_reachable: bool | None = None,
) -> ReadinessReport:
    relay_active = settings.relay_enabled
    nats_check = True
    if relay_active:
        nats_check = nats_reachable is True

    checks = {
        "production_gates": settings.environment.value != "production"
        or (
            settings.adr_0004_credentials_configured
            and settings.adr_0007_staging_gates_satisfied
        ),
        "postgres_adapter_present": persistence_wired,
        "database_configured": bool(settings.database_url),
        "database_reachable": engine is not None and ping_database(engine),
        "relay_configuration_valid": settings.relay_configuration_valid(),
        "nats_reachable": nats_check,
        "cdc_adapter_deferred": True,
        "nats_publisher_active": relay_active,
    }
    blockers: list[str] = []
    if not checks["production_gates"]:
        blockers.append("production_gates_unset")
    if not checks["postgres_adapter_present"]:
        blockers.append("postgres_adapter_not_wired")
    if settings.environment.value != "test" and not checks["database_reachable"]:
        blockers.append("database_unreachable")
    if relay_active and not checks["relay_configuration_valid"]:
        blockers.append("relay_configuration_invalid")
    if relay_active and not checks["nats_reachable"]:
        blockers.append("nats_unreachable")
    blockers.append("live_cdc_adapter")
    if not relay_active:
        blockers.append("nats_publisher_not_enabled")

    ready = (
        checks["production_gates"]
        and checks["postgres_adapter_present"]
        and checks["relay_configuration_valid"]
        and (
            settings.environment.value == "test"
            or checks["database_reachable"]
        )
        and (not relay_active or checks["nats_reachable"])
    )
    return ReadinessReport(ready=ready, checks=checks, blockers=tuple(blockers))
