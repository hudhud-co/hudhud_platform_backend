"""FastAPI composition root — health, readiness, and recovery command API."""

from __future__ import annotations

from fastapi import FastAPI

from pickup import __version__
from pickup.api.health import router as health_router
from pickup.api.recovery import router as recovery_router
from pickup.application.readiness import evaluate_readiness
from pickup.application.recovery_service import PickupRecoveryService
from pickup.config import PickupSettings, RuntimeEnvironment, load_settings
from pickup.infrastructure.authorizers.default_deny import DefaultDenyRecoveryAuthorizer
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyRecoveryUnitOfWork
from pickup.infrastructure.unavailable_shipment_eligibility import (
    UnavailableShipmentEligibilityAdapter,
)
from pickup.ports.recovery_authorizer import RecoveryAuthorizer
from pickup.ports.repository import RecoveryUnitOfWork
from pickup.ports.shipment_eligibility import ShipmentEligibilityPort


def create_app(
    settings: PickupSettings | None = None,
    *,
    unit_of_work: RecoveryUnitOfWork | None = None,
    shipment_eligibility: ShipmentEligibilityPort | None = None,
    recovery_authorizer: RecoveryAuthorizer | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    resolved_uow = unit_of_work

    if resolved_uow is None and resolved.database_url:
        engine = build_engine(resolved.database_url)
        session_factory = build_session_factory(engine)
        resolved_uow = SqlAlchemyRecoveryUnitOfWork(session_factory=session_factory)
        persistence_wired = True
    elif resolved_uow is not None:
        persistence_wired = True

    resolved_eligibility = shipment_eligibility or UnavailableShipmentEligibilityAdapter()
    resolved_authorizer = recovery_authorizer or DefaultDenyRecoveryAuthorizer()

    recovery_service: PickupRecoveryService | None = None
    if resolved_uow is not None:
        recovery_service = PickupRecoveryService(
            unit_of_work=resolved_uow,
            shipment_eligibility=resolved_eligibility,
        )

    app = FastAPI(
        title="HUDHUD Pickup",
        version=__version__,
        description="PickupTask recovery command API (does not mutate Shipment custody)",
    )
    app.include_router(health_router)
    if recovery_service is not None:
        app.include_router(recovery_router)

    app.state.settings = resolved
    app.state.engine = engine
    app.state.recovery_service = recovery_service
    app.state.recovery_authorizer = resolved_authorizer
    app.state.shipment_eligibility = resolved_eligibility
    app.state.readiness_report = evaluate_readiness(
        settings=resolved,
        engine=engine,
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        shipment_eligibility_configured=resolved_eligibility.is_production_ready,
    )
    return app


app = create_app()
