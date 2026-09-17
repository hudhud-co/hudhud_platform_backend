"""FastAPI composition root for the Merchant service."""

from __future__ import annotations

from fastapi import FastAPI

from merchant import __version__
from merchant.api.health import router as health_router
from merchant.api.routes import router as merchant_router
from merchant.application.application_service import (
    ApplicationPolicy,
    MerchantApplicationService,
)
from merchant.application.catalogue_service import CatalogueService
from merchant.application.inbox_service import InboxService
from merchant.application.label_stock_service import LabelStockService
from merchant.application.readiness import evaluate_readiness
from merchant.application.store_service import StoreService
from merchant.application.team_service import TeamService
from merchant.config import MerchantSettings, RuntimeEnvironment, load_settings
from merchant.infrastructure.authorizers.identity import (
    DefaultDenyMerchantAuthorizer,
    IdentityMerchantAuthorizer,
)
from merchant.infrastructure.memory import InMemoryMerchantUnitOfWork
from merchant.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from merchant.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyMerchantUnitOfWork,
)
from merchant.ports.authorization import MerchantAuthorizer
from merchant.ports.repository import MerchantUnitOfWork


def create_app(
    settings: MerchantSettings | None = None,
    *,
    unit_of_work: MerchantUnitOfWork | None = None,
    authorizer: MerchantAuthorizer | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    uow = unit_of_work
    persistence_wired = uow is not None
    if uow is None and resolved.database_url:
        engine = build_engine(resolved.database_url)
        uow = SqlAlchemyMerchantUnitOfWork(session_factory=build_session_factory(engine))
        persistence_wired = True
    if uow is None:
        uow = InMemoryMerchantUnitOfWork()

    resolved_authorizer = authorizer or _build_authorizer(resolved)

    app = FastAPI(
        title="HUDHUD Merchant",
        version=__version__,
        description=(
            "Merchant application and activation, stores, team, label stock "
            "and standing shipment policy"
        ),
    )
    app.include_router(health_router)
    app.include_router(merchant_router)

    app.state.settings = resolved
    app.state.engine = engine
    app.state.unit_of_work = uow
    app.state.authorizer = resolved_authorizer
    app.state.application_service = MerchantApplicationService(
        uow,
        policy=ApplicationPolicy(
            required_attributes=resolved.application_required_attributes
        ),
        outbox_max_attempts=resolved.outbox_max_attempts,
    )
    app.state.store_service = StoreService(uow)
    app.state.team_service = TeamService(
        uow, outbox_max_attempts=resolved.outbox_max_attempts
    )
    app.state.label_stock_service = LabelStockService(uow)
    app.state.catalogue_service = CatalogueService(uow)
    app.state.inbox_service = InboxService(uow)
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        extra_checks={
            # Not a fault — the platform is waiting on a business decision (MER-02).
            # Surfacing it on /ready is how an operator learns the flow is still closed.
            "merchant_application_data_set_decided": (
                resolved.application_submission_enabled
            ),
        },
    )
    return app


def _build_authorizer(settings: MerchantSettings) -> MerchantAuthorizer:
    """Authorize through Identity when configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyMerchantAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityMerchantAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


app = create_app()
