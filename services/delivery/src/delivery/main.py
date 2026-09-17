"""FastAPI composition root for the Delivery service.

Three things are composed fail-closed here, and each refusal is deliberate:

* with no Identity configured the authorizer denies everything;
* with no Workforce configured no driver is eligible to be given work (ADR-0013);
* with no decided code length, code verification answers 501 while the rest of the
  doorstep — the wait, the ID fallback, the seal, the inspection, payment and the three
  outcomes — works normally.

Production additionally refuses to start without a database, an Identity, a Workforce and
a delivery-code HMAC key. It does *not* refuse to start without a decided code length:
that is a business decision, and a service that will not boot is worse than one that
declines a single operation.
"""

from __future__ import annotations

from fastapi import FastAPI

from delivery import __version__
from delivery.api.health import router as health_router
from delivery.api.routes import router as delivery_router
from delivery.application.readiness import evaluate_readiness
from delivery.config import DeliverySettings, RuntimeEnvironment, load_settings
from delivery.domain.delivery_code import DeliveryCodePolicy
from delivery.domain.id_evidence import IdEvidencePolicy
from delivery.infrastructure.authorizers.identity import (
    DefaultDenyDeliveryAuthorizer,
    IdentityDeliveryAuthorizer,
)
from delivery.infrastructure.authorizers.workforce import (
    DenyingWorkforceEligibility,
    HttpWorkforceEligibility,
)
from delivery.infrastructure.memory import InMemoryUnitOfWork
from delivery.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from delivery.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyDeliveryUnitOfWork,
)
from delivery.ports.authorization import DeliveryAuthorizer, WorkforceEligibilityPort
from delivery.ports.repository import DeliveryUnitOfWork


def create_app(
    settings: DeliverySettings | None = None,
    *,
    unit_of_work: DeliveryUnitOfWork | None = None,
    authorizer: DeliveryAuthorizer | None = None,
    workforce: WorkforceEligibilityPort | None = None,
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
            return SqlAlchemyDeliveryUnitOfWork(session_factory=_session_factory)

        persistence_wired = True
    else:
        _database = InMemoryUnitOfWork().database

        def unit_of_work_factory():
            return InMemoryUnitOfWork(_database)

    resolved_authorizer = authorizer or _build_authorizer(resolved)
    resolved_workforce = workforce or _build_workforce(resolved)

    code_policy = DeliveryCodePolicy(length=resolved.delivery_code_length)
    id_policy = IdEvidencePolicy(
        retention_decided=resolved.id_photo_retention_decided
    )

    app = FastAPI(
        title="HUDHUD Delivery",
        version=__version__,
        description=(
            "Last-mile manifests and custody, the doorstep sequence, receiver "
            "verification, payment at the door, delivery outcomes and courier ratings"
        ),
    )
    app.include_router(health_router)
    app.include_router(delivery_router)


    app.state.settings = resolved
    app.state.engine = engine
    # A *factory*, never a unit of work. A unit of work holds one request's session and
    # one request's pending writes; sharing the instance meant concurrent requests fought
    # over one session — measured against PostgreSQL, 1 of 40 succeeded. The application
    # services are built per request too, because each holds a reference to it.
    # `tests/architecture/test_request_scoped_state.py` keeps it that way.
    app.state.unit_of_work_factory = unit_of_work_factory
    app.state.authorizer = resolved_authorizer
    app.state.workforce = resolved_workforce
    app.state.code_policy = code_policy
    app.state.id_policy = id_policy
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        extra_checks={
            "workforce_eligibility_configured": resolved_workforce.is_production_ready,
            "delivery_code_hmac_key_set": bool(resolved.delivery_code_hmac_key),
            # DRV-L05 — reported, so an operator can see why code verification refuses,
            # rather than having to read the 501 and guess.
            "delivery_code_length_decided": code_policy.is_decided,
        },
    )
    return app


def _build_authorizer(settings: DeliverySettings) -> DeliveryAuthorizer:
    """Authorize through Identity when configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyDeliveryAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityDeliveryAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


def _build_workforce(settings: DeliverySettings) -> WorkforceEligibilityPort:
    """Ask Workforce over HTTP when configured; otherwise no driver is eligible."""
    if not settings.workforce_eligibility_enabled:
        return DenyingWorkforceEligibility()
    assert settings.workforce_base_url is not None
    assert settings.workforce_service_credential is not None
    return HttpWorkforceEligibility(
        base_url=settings.workforce_base_url,
        service_credential=settings.workforce_service_credential,
    )


app = create_app()
