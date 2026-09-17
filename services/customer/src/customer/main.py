"""FastAPI composition root for the Customer service."""

from __future__ import annotations

from fastapi import FastAPI

from customer import __version__
from customer.api.health import router as health_router
from customer.api.routes import router as customer_router
from customer.application.address_book_service import AddressBookService
from customer.application.profile_service import CustomerProfileService, LegalPolicy
from customer.application.readiness import evaluate_readiness
from customer.config import CustomerSettings, RuntimeEnvironment, load_settings
from customer.infrastructure.authorizers import (
    DefaultDenyCustomerAuthorizer,
    IdentityCustomerAuthorizer,
)
from customer.infrastructure.memory import InMemoryCustomerUnitOfWork
from customer.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from customer.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyCustomerUnitOfWork,
)
from customer.ports.authorization import CustomerAuthorizer
from customer.ports.repository import CustomerUnitOfWork


def create_app(
    settings: CustomerSettings | None = None,
    *,
    unit_of_work: CustomerUnitOfWork | None = None,
    authorizer: CustomerAuthorizer | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    uow = unit_of_work
    persistence_wired = uow is not None
    if uow is None and resolved.database_url:
        engine = build_engine(resolved.database_url)
        uow = SqlAlchemyCustomerUnitOfWork(session_factory=build_session_factory(engine))
        persistence_wired = True
    if uow is None:
        uow = InMemoryCustomerUnitOfWork()

    resolved_authorizer = authorizer or _build_authorizer(resolved)
    legal = LegalPolicy(
        terms_version=resolved.terms_version, privacy_version=resolved.privacy_version
    )

    app = FastAPI(
        title="HUDHUD Customer",
        version=__version__,
        description="Customer profile, legal acceptance and address book",
    )
    app.include_router(health_router)
    app.include_router(customer_router)

    app.state.settings = resolved
    app.state.engine = engine
    app.state.unit_of_work = uow
    app.state.authorizer = resolved_authorizer
    app.state.profile_service = CustomerProfileService(uow, legal_policy=legal)
    app.state.address_book_service = AddressBookService(uow)
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
    )
    return app


def _build_authorizer(settings: CustomerSettings) -> CustomerAuthorizer:
    """Authorize through Identity when configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyCustomerAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityCustomerAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


app = create_app()
