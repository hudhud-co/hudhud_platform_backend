"""FastAPI composition root — health, readiness, and recovery command API."""

from __future__ import annotations

from fastapi import FastAPI

from pickup import __version__
from pickup.api.driver_work import router as driver_work_router
from pickup.api.handover import router as handover_router
from pickup.api.health import router as health_router
from pickup.api.offline import router as offline_router
from pickup.api.recovery import router as recovery_router
from pickup.application.readiness import evaluate_readiness
from pickup.composition import build_driver_services
from pickup.config import PickupSettings, RuntimeEnvironment, load_settings
from pickup.infrastructure.authorizers.default_deny import DefaultDenyRecoveryAuthorizer
from pickup.infrastructure.authorizers.http_introspection import (
    HttpIntrospectionTransport,
)
from pickup.infrastructure.authorizers.identity_introspection import (
    IdentityIntrospectionAuthorizer,
)
from pickup.infrastructure.authorizers.pickup_default_deny import (
    DefaultDenyPickupAuthorizer,
)
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyRecoveryUnitOfWork
from pickup.infrastructure.unavailable_shipment_eligibility import (
    UnavailableShipmentEligibilityAdapter,
)
from pickup.ports.authorization import PickupAuthorizer
from pickup.ports.recovery_authorizer import RecoveryAuthorizer
from pickup.ports.repository import RecoveryUnitOfWork
from pickup.ports.shipment_eligibility import ShipmentEligibilityPort


def create_app(
    settings: PickupSettings | None = None,
    *,
    unit_of_work: RecoveryUnitOfWork | None = None,
    shipment_eligibility: ShipmentEligibilityPort | None = None,
    recovery_authorizer: RecoveryAuthorizer | None = None,
    pickup_authorizer: PickupAuthorizer | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    unit_of_work_factory = None

    if unit_of_work is None and resolved.database_url:
        engine = build_engine(resolved.database_url)
        _session_factory = build_session_factory(engine)

        def unit_of_work_factory():
            # One per request. This unit of work keeps a session on itself, so sharing
            # the instance made concurrent requests collide on it.
            return SqlAlchemyRecoveryUnitOfWork(session_factory=_session_factory)

        persistence_wired = True
    elif unit_of_work is not None:
        make = getattr(unit_of_work, "new_unit_of_work", None)
        unit_of_work_factory = make or (lambda: unit_of_work)
        persistence_wired = True

    resolved_eligibility = shipment_eligibility or UnavailableShipmentEligibilityAdapter()
    resolved_authorizer = recovery_authorizer or DefaultDenyRecoveryAuthorizer()
    resolved_pickup_authorizer = pickup_authorizer or _build_pickup_authorizer(resolved)

    # Which routers to mount is a question about *configuration*, not about any one
    # request's state, so it is answered once here with a throwaway unit of work that is
    # never stored. The services themselves are built per request in
    # `api/driver_dependencies.py`.
    _probe = build_driver_services(
        resolved, unit_of_work_factory() if unit_of_work_factory else None
    )
    driver_features_available = _probe is not None
    handover_available = _probe is not None and _probe.verification_service is not None
    offline_available = _probe is not None and _probe.offline_sync_service is not None

    app = FastAPI(
        title="HUDHUD Pickup",
        version=__version__,
        description="PickupTask recovery command API (does not mutate Shipment custody)",
    )
    app.include_router(health_router)
    if unit_of_work_factory is not None:
        app.include_router(recovery_router)
    if driver_features_available:
        app.include_router(driver_work_router)
        if handover_available:
            app.include_router(handover_router)
        if offline_available:
            app.include_router(offline_router)

    app.state.settings = resolved
    app.state.engine = engine
    # A factory, never a unit of work, and no application service either — each holds a
    # reference to one request's session. Enforced by
    # `tests/architecture/test_request_scoped_state.py`.
    app.state.unit_of_work_factory = unit_of_work_factory
    app.state.recovery_authorizer = resolved_authorizer
    app.state.pickup_authorizer = resolved_pickup_authorizer
    app.state.shipment_eligibility = resolved_eligibility
    app.state.readiness_report = evaluate_readiness(
        settings=resolved,
        engine=engine,
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        driver_command_authorization_configured=(
            resolved_pickup_authorizer.is_production_ready
        ),
        shipment_eligibility_configured=resolved_eligibility.is_production_ready,
    )
    return app


def _build_pickup_authorizer(settings: PickupSettings) -> PickupAuthorizer:
    """Authorize through Identity when it is configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyPickupAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityIntrospectionAuthorizer(
        HttpIntrospectionTransport(
            base_url=settings.identity_base_url,
            service_credential=settings.identity_service_credential,
        )
    )


app = create_app()
