"""FastAPI health and readiness routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "legacy_event_bridge"}


@router.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready", "service": "legacy_event_bridge"}
