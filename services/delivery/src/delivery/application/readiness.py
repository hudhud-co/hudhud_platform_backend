"""Readiness evaluation for the Delivery service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, bool]
    blockers: tuple[str, ...] = ()


def evaluate_readiness(
    *,
    persistence_wired: bool,
    authorization_configured: bool,
    extra_checks: dict[str, bool] | None = None,
) -> ReadinessReport:
    checks = {
        "persistence_wired": persistence_wired,
        "authorization_configured": authorization_configured,
    }
    checks.update(extra_checks or {})
    blockers = tuple(sorted(name for name, ok in checks.items() if not ok))
    return ReadinessReport(ready=not blockers, checks=checks, blockers=blockers)
