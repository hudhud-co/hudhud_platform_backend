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
) -> ReadinessReport:
    checks = {
        "production_gates": settings.environment.value != "production"
        or (
            settings.adr_0004_credentials_configured
            and settings.adr_0007_staging_gates_satisfied
        ),
        "postgres_adapter_present": persistence_wired,
        "database_configured": bool(settings.database_url),
        "database_reachable": engine is not None and ping_database(engine),
        "cdc_adapter_deferred": True,
        "nats_publisher_deferred": True,
    }
    blockers: list[str] = []
    if not checks["production_gates"]:
        blockers.append("production_gates_unset")
    if not checks["postgres_adapter_present"]:
        blockers.append("postgres_adapter_not_wired")
    if settings.environment.value != "test" and not checks["database_reachable"]:
        blockers.append("database_unreachable")
    blockers.extend(["live_cdc_adapter", "nats_publisher"])
    ready = (
        checks["production_gates"]
        and checks["postgres_adapter_present"]
        and (
            settings.environment.value == "test"
            or checks["database_reachable"]
        )
    )
    return ReadinessReport(ready=ready, checks=checks, blockers=tuple(blockers))
