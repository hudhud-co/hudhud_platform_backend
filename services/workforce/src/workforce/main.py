"""FastAPI composition root for the Workforce service."""

from __future__ import annotations

from fastapi import FastAPI

from workforce import __version__
from workforce.api.health import router as health_router
from workforce.api.routes import router as workforce_router
from workforce.application.readiness import evaluate_readiness
from workforce.config import RuntimeEnvironment, WorkforceSettings, load_settings
from workforce.infrastructure.authorizers.identity import (
    DefaultDenyWorkforceAuthorizer,
    IdentityWorkforceAuthorizer,
)
from workforce.infrastructure.memory import InMemoryWorkforceUnitOfWork
from workforce.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from workforce.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyWorkforceUnitOfWork,
)
from workforce.ports.authorization import WorkforceAuthorizer
from workforce.ports.repository import WorkforceUnitOfWork


def create_app(
    settings: WorkforceSettings | None = None,
    *,
    unit_of_work: WorkforceUnitOfWork | None = None,
    authorizer: WorkforceAuthorizer | None = None,
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
            return SqlAlchemyWorkforceUnitOfWork(session_factory=_session_factory)

        persistence_wired = True
    else:
        _database = InMemoryWorkforceUnitOfWork().database

        def unit_of_work_factory():
            return InMemoryWorkforceUnitOfWork(_database)

    resolved_authorizer = authorizer or _build_authorizer(resolved)

    app = FastAPI(
        title="HUDHUD Workforce",
        version=__version__,
        description=(
            "Driver onboarding and office verification, shift patterns, attendance, "
            "lateness blocks, leave and work-assignment eligibility"
        ),
    )
    app.include_router(health_router)
    app.include_router(workforce_router)

    app.state.settings = resolved
    app.state.engine = engine
    # A *factory*, never a unit of work. A unit of work holds one request's session and
    # one request's pending writes; sharing the instance meant concurrent requests fought
    # over one session — measured against PostgreSQL, 1 of 40 succeeded. The application
    # services are built per request too, because each holds a reference to it.
    # `tests/architecture/test_request_scoped_state.py` keeps it that way.
    app.state.unit_of_work_factory = unit_of_work_factory
    app.state.authorizer = resolved_authorizer
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
    )
    return app


def _build_authorizer(settings: WorkforceSettings) -> WorkforceAuthorizer:
    """Authorize through Identity when configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyWorkforceAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityWorkforceAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


app = create_app()
