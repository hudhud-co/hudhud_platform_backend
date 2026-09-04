"""FastAPI health and readiness routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "pickup"}


@router.get("/ready", response_model=None)
def ready(request: Request) -> JSONResponse | dict[str, object]:
    report = getattr(request.app.state, "readiness_report", None)
    if report is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "service": "pickup",
                "blockers": ["readiness_probe_not_initialized"],
            },
        )
    payload = {
        "status": "ready" if report.ready else "not_ready",
        "service": "pickup",
        "checks": report.checks,
        "blockers": list(report.blockers),
    }
    if report.ready:
        return payload
    return JSONResponse(status_code=503, content=payload)
