"""FastAPI composition root for the Identity service.

Fail-closed composition: with no signing key the authentication services are not built at
all, so every route answers 503 rather than running with a default key. With no service
credential configured, introspection denies every caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from identity import __version__
from identity.api.health import router as health_router
from identity.api.routes import router as identity_router
from identity.application.authentication_service import (
    AuthenticationService,
    BootstrapPolicy,
    OtpPolicy,
)
from identity.application.introspection_service import IntrospectionService
from identity.application.readiness import evaluate_readiness
from identity.application.role_service import RoleService
from identity.config import IdentitySettings, RuntimeEnvironment, load_settings
from identity.infrastructure.adapters import (
    ConsoleOtpDelivery,
    DenyAllServiceCredentials,
    SharedSecretServiceCredentials,
    UnavailableOtpDelivery,
)
from identity.infrastructure.memory import InMemoryIdentityUnitOfWork
from identity.infrastructure.persistence.session import build_engine, build_session_factory
from identity.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyIdentityUnitOfWork,
)
from identity.ports.otp_delivery import OtpDeliveryPort
from identity.ports.repository import IdentityUnitOfWork
from identity.ports.service_credentials import ServiceCredentialVerifier


@dataclass(frozen=True, slots=True)
class _Services:
    authentication_service: AuthenticationService
    introspection_service: IntrospectionService
    role_service: RoleService


def create_app(
    settings: IdentitySettings | None = None,
    *,
    unit_of_work: IdentityUnitOfWork | None = None,
    otp_delivery: OtpDeliveryPort | None = None,
    service_credentials: ServiceCredentialVerifier | None = None,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    uow = unit_of_work
    persistence_wired = uow is not None
    if uow is None and resolved.database_url:
        engine = build_engine(resolved.database_url)
        uow = SqlAlchemyIdentityUnitOfWork(
            session_factory=build_session_factory(engine)
        )
        persistence_wired = True
    if uow is None:
        uow = InMemoryIdentityUnitOfWork()

    delivery = otp_delivery or _build_delivery(resolved)
    credentials = service_credentials or (
        SharedSecretServiceCredentials(resolved.service_credentials)
        if resolved.service_credentials
        else DenyAllServiceCredentials()
    )

    services = _build_services(resolved, uow, delivery, credentials)

    app = FastAPI(
        title="HUDHUD Identity",
        version=__version__,
        description="Authentication, principals, role grants and token introspection",
    )
    app.include_router(health_router)
    if services is not None:
        app.include_router(identity_router)

    app.state.settings = resolved
    app.state.engine = engine
    app.state.unit_of_work = uow
    app.state.authentication_service = (
        services.authentication_service if services else None
    )
    app.state.introspection_service = services.introspection_service if services else None
    app.state.role_service = services.role_service if services else None
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=credentials.is_production_ready,
        extra_checks={
            "signing_key_configured": resolved.authentication_enabled,
            "otp_delivery_configured": delivery.is_production_ready,
        },
    )
    return app


def _build_delivery(settings: IdentitySettings) -> OtpDeliveryPort:
    if settings.otp_delivery_channel == "console":
        return ConsoleOtpDelivery()
    return UnavailableOtpDelivery()


def _build_services(
    settings: IdentitySettings,
    uow: IdentityUnitOfWork,
    delivery: OtpDeliveryPort,
    credentials: ServiceCredentialVerifier,
) -> _Services | None:
    if not settings.signing_key:
        # Without a key no hash can be produced; issuing codes would be unsafe.
        return None
    return _Services(
        authentication_service=AuthenticationService(
            uow,
            signing_key=settings.signing_key,
            otp_delivery=delivery,
            policy=OtpPolicy(
                code_ttl_seconds=settings.otp_code_ttl_seconds,
                max_attempts=settings.otp_max_attempts,
                max_codes_per_window=settings.otp_max_codes_per_window,
                rate_limit_window_seconds=settings.otp_rate_limit_window_seconds,
                session_ttl_seconds=settings.session_ttl_seconds,
            ),
            bootstrap=BootstrapPolicy(
                operations_phone=settings.bootstrap_operations_phone
            ),
        ),
        introspection_service=IntrospectionService(
            uow, signing_key=settings.signing_key, service_credentials=credentials
        ),
        role_service=RoleService(uow),
    )


app = create_app()
