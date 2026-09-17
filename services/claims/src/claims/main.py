"""FastAPI composition root for the Claims service."""

from __future__ import annotations

from fastapi import FastAPI

from claims import __version__
from claims.api.health import router as health_router
from claims.api.routes import router as claims_router
from claims.application.readiness import evaluate_readiness
from claims.config import ClaimsSettings, RuntimeEnvironment, load_settings
from claims.domain.high_value import HighValuePolicy
from claims.domain.money import Currency, Money
from claims.infrastructure.authorizers.identity import (
    DefaultDenyClaimsAuthorizer,
    IdentityClaimsAuthorizer,
)
from claims.infrastructure.memory import InMemoryUnitOfWork
from claims.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from claims.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyClaimsUnitOfWork,
)
from claims.ports.authorization import ClaimsAuthorizer
from claims.ports.repository import ClaimsUnitOfWork


def create_app(
    settings: ClaimsSettings | None = None,
    *,
    unit_of_work: ClaimsUnitOfWork | None = None,
    authorizer: ClaimsAuthorizer | None = None,
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
            return SqlAlchemyClaimsUnitOfWork(session_factory=_session_factory)

        persistence_wired = True
    else:
        _database = InMemoryUnitOfWork().database

        def unit_of_work_factory():
            return InMemoryUnitOfWork(_database)

    resolved_authorizer = authorizer or _build_authorizer(resolved)

    app = FastAPI(
        title="HUDHUD Claims",
        version=__version__,
        description=(
            "Compensation claims, the support conversation that goes with one, "
            "driver incident reports, and the operations view over both"
        ),
    )
    app.include_router(health_router)
    app.include_router(claims_router)

    app.state.settings = resolved
    app.state.engine = engine
    # A *factory*, never a unit of work. A unit of work holds one request's session and
    # one request's pending writes; sharing the instance meant concurrent requests fought
    # over one session — measured against PostgreSQL, 1 of 40 succeeded. The application
    # services are built per request too, because each holds a reference to it.
    # `tests/architecture/test_request_scoped_state.py` keeps it that way.
    app.state.unit_of_work_factory = unit_of_work_factory
    app.state.authorizer = resolved_authorizer
    app.state.high_value_policy = _build_high_value_policy(resolved)
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        extra_checks={
            # CLM-08 is an unresolved v6.3 Open Item, and this check says so out loud
            # rather than letting an undecided threshold look like a healthy service.
            # Everything else here works without it; only `is_high_value` refuses.
            "high_value_threshold_decided": resolved.high_value_threshold_decided,
        },
    )
    return app


def _build_authorizer(settings: ClaimsSettings) -> ClaimsAuthorizer:
    if not settings.identity_authorization_enabled:
        return DefaultDenyClaimsAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityClaimsAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


def _build_high_value_policy(settings: ClaimsSettings) -> HighValuePolicy:
    """CLM-08 — undecided unless a deployment has set the number (see `config.py`)."""
    if settings.high_value_threshold_minor_units is None:
        return HighValuePolicy()
    return HighValuePolicy(
        threshold=Money(settings.high_value_threshold_minor_units, Currency.IQD)
    )


app = create_app()
