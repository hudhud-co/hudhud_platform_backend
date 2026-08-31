"""Readiness evaluation for Audit runtime gates."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from audit.config import AuditSettings
from audit.infrastructure.persistence.session import ping_database


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, bool] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()


def evaluate_readiness(
    *,
    settings: AuditSettings,
    engine: Engine | None,
    persistence_wired: bool,
) -> ReadinessReport:
    checks = {
        "production_gates": settings.environment.value != "production"
        or settings.adr_0004_credentials_configured,
        "postgres_adapter_present": persistence_wired,
        "database_configured": bool(settings.database_url),
        "database_reachable": engine is not None and ping_database(engine),
        "nats_consumer_deferred": not settings.nats_enabled,
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
    if settings.nats_enabled:
        blockers.append("live_nats_consumer_not_started")
    ready = (
        checks["production_gates"]
        and checks["postgres_adapter_present"]
        and (settings.environment.value == "test" or checks["database_reachable"])
        and not settings.nats_enabled
    )
    return ReadinessReport(ready=ready, checks=checks, blockers=tuple(blockers))
