"""FastAPI composition root for the Ordering service."""

from __future__ import annotations

from fastapi import FastAPI

from ordering import __version__
from ordering.api.health import router as health_router
from ordering.api.routes import public_router
from ordering.api.routes import router as ordering_router
from ordering.application.amendment_service import AmendmentService
from ordering.application.catalogue_service import GoodsCatalogueService
from ordering.application.inbox_service import InboxService
from ordering.application.pricing_service import PricingService, ServiceabilityPolicy
from ordering.application.public_tracking_service import PublicTrackingService
from ordering.application.readiness import evaluate_readiness
from ordering.application.send_service import SendService
from ordering.config import OrderingSettings, RuntimeEnvironment, load_settings
from ordering.infrastructure.authorizers.identity import (
    DefaultDenyOrderingAuthorizer,
    IdentityOrderingAuthorizer,
)
from ordering.infrastructure.authorizers.merchant_access import (
    HttpMerchantAccess,
    NoMerchantAccess,
)
from ordering.infrastructure.memory import InMemoryOrderingUnitOfWork
from ordering.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from ordering.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyOrderingUnitOfWork,
)
from ordering.ports.authorization import MerchantAccessPort, OrderingAuthorizer
from ordering.ports.repository import OrderingUnitOfWork


def create_app(
    settings: OrderingSettings | None = None,
    *,
    unit_of_work: OrderingUnitOfWork | None = None,
    authorizer: OrderingAuthorizer | None = None,
    merchant_access: MerchantAccessPort | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    uow = unit_of_work
    persistence_wired = uow is not None
    if uow is None and resolved.database_url:
        engine = build_engine(resolved.database_url)
        uow = SqlAlchemyOrderingUnitOfWork(session_factory=build_session_factory(engine))
        persistence_wired = True
    if uow is None:
        uow = InMemoryOrderingUnitOfWork()

    resolved_authorizer = authorizer or _build_authorizer(resolved)
    resolved_merchant = merchant_access or _build_merchant_access(resolved)

    app = FastAPI(
        title="HUDHUD Ordering",
        version=__version__,
        description=(
            "Orders, shipment registration, labels, pickup booking, pricing, "
            "serviceability and public tracking lookup"
        ),
    )
    app.include_router(health_router)
    app.include_router(ordering_router)
    app.include_router(public_router)

    app.state.settings = resolved
    app.state.engine = engine
    app.state.unit_of_work = uow
    app.state.authorizer = resolved_authorizer
    app.state.merchant_access = resolved_merchant
    app.state.send_service = SendService(
        uow,
        outbox_max_attempts=resolved.outbox_max_attempts,
        require_prohibited_acknowledgement=resolved.require_prohibited_acknowledgement,
    )
    app.state.amendment_service = AmendmentService(
        uow, outbox_max_attempts=resolved.outbox_max_attempts
    )
    app.state.pricing_service = PricingService(
        uow,
        serviceability=ServiceabilityPolicy(
            serviceable_governorates=frozenset(resolved.serviceable_governorates)
        ),
    )
    app.state.catalogue_service = GoodsCatalogueService(uow)
    app.state.tracking_service = PublicTrackingService(uow)
    app.state.inbox_service = InboxService(uow)
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        extra_checks={
            "merchant_access_configured": resolved_merchant.is_production_ready,
            # Not a fault — an operator has not yet said where Hudhud delivers. Until
            # they do, every quote and every serviceability check refuses.
            "serviceability_configured": resolved.serviceability_configured,
        },
    )
    return app


def _build_authorizer(settings: OrderingSettings) -> OrderingAuthorizer:
    """Authorize through Identity when configured; otherwise stay fail-closed."""
    if not settings.identity_authorization_enabled:
        return DefaultDenyOrderingAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityOrderingAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


def _build_merchant_access(settings: OrderingSettings) -> MerchantAccessPort:
    """Read store access from Merchant when configured; otherwise report none."""
    if not settings.merchant_access_enabled:
        return NoMerchantAccess()
    assert settings.merchant_base_url is not None
    assert settings.merchant_service_credential is not None
    return HttpMerchantAccess(
        base_url=settings.merchant_base_url,
        service_credential=settings.merchant_service_credential,
    )


app = create_app()
