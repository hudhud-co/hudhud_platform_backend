"""FastAPI composition root for the Hub service."""

from __future__ import annotations

from fastapi import FastAPI

from hub import __version__
from hub.api.health import router as health_router
from hub.api.routes import router as hub_router
from hub.application.drop_off_service import DropOffPolicy
from hub.application.readiness import evaluate_readiness
from hub.config import HubSettings, RuntimeEnvironment, load_settings
from hub.infrastructure.authorizers.identity import (
    DefaultDenyHubAuthorizer,
    IdentityHubAuthorizer,
)
from hub.infrastructure.memory import InMemoryHubUnitOfWork
from hub.infrastructure.persistence.session import build_engine, build_session_factory
from hub.infrastructure.persistence.sqlalchemy_store import SqlAlchemyHubUnitOfWork
from hub.ports.authorization import HubAuthorizer
from hub.ports.repository import HubUnitOfWork


def create_app(
    settings: HubSettings | None = None,
    *,
    unit_of_work: HubUnitOfWork | None = None,
    authorizer: HubAuthorizer | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    if unit_of_work is not None:
        # A test injected a store. Its *rows* are shared, as a database's are; each
        # request still gets its own transaction over them.
        make = getattr(unit_of_work, "new_unit_of_work", None)
        unit_of_work_factory = make or (lambda: unit_of_work)
        persistence_wired = True
    elif resolved.database_url:
        engine = build_engine(resolved.database_url)
        _session_factory = build_session_factory(engine)

        def unit_of_work_factory():
            return SqlAlchemyHubUnitOfWork(session_factory=_session_factory)

        persistence_wired = True
    else:
        _database = InMemoryHubUnitOfWork().database

        def unit_of_work_factory():
            return InMemoryHubUnitOfWork(_database)

    resolved_authorizer = authorizer or _build_authorizer(resolved)

    app = FastAPI(
        title="HUDHUD Hub",
        version=__version__,
        description=(
            "Customer drop-off intake, hub processing and sorting, consignments, "
            "seal checks and inter-city linehaul"
        ),
    )
    app.include_router(health_router)
    app.include_router(hub_router)

    app.state.settings = resolved
    app.state.engine = engine
    # A *factory*, never a unit of work. A unit of work holds one request's session and
    # one request's pending writes; sharing the instance meant concurrent requests fought
    # over one session — measured against PostgreSQL, 1 of 40 succeeded. The application
    # services are built per request too, because each holds a reference to it.
    # `tests/architecture/test_request_scoped_state.py` keeps it that way.
    app.state.unit_of_work_factory = unit_of_work_factory
    app.state.authorizer = resolved_authorizer
    app.state.drop_off_policy = DropOffPolicy(
        hold_days=resolved.drop_off_hold_days
    )
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
    )
    return app


def _build_authorizer(settings: HubSettings) -> HubAuthorizer:
    """Authorize through Identity when configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyHubAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityHubAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


app = create_app()
