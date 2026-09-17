"""FastAPI composition root for the Notification service."""

from __future__ import annotations

from fastapi import FastAPI

from notification import __version__
from notification.api.health import router as health_router
from notification.api.routes import router as notification_router
from notification.application.readiness import evaluate_readiness
from notification.config import (
    NotificationSettings,
    RuntimeEnvironment,
    load_settings,
)
from notification.domain.value_objects import Channel
from notification.infrastructure.authorizers.identity import (
    DefaultDenyNotificationAuthorizer,
    IdentityNotificationAuthorizer,
)
from notification.infrastructure.memory import InMemoryNotificationUnitOfWork
from notification.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)
from notification.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyNotificationUnitOfWork,
)
from notification.infrastructure.transports import UnavailableTransport
from notification.ports.authorization import NotificationAuthorizer
from notification.ports.repository import NotificationUnitOfWork
from notification.ports.transport import ChannelTransport

#: Bodies are configuration. These are the codes the policy refers to; a deployment that
#: has not loaded a body for one of them will fail loudly at render time rather than send
#: an empty message.
DEFAULT_TEMPLATES: dict[str, str] = {}

#: Development fallback only, used when nothing else is configured. Never production-ready.
_DEV_KEY = "notification-recipient-key-dev-only"


def create_app(
    settings: NotificationSettings | None = None,
    *,
    unit_of_work: NotificationUnitOfWork | None = None,
    authorizer: NotificationAuthorizer | None = None,
    transports: dict[Channel, ChannelTransport] | None = None,
    templates: dict[str, str] | None = None,
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
            return SqlAlchemyNotificationUnitOfWork(session_factory=_session_factory)

        persistence_wired = True
    else:
        _database = InMemoryNotificationUnitOfWork().database

        def unit_of_work_factory():
            return InMemoryNotificationUnitOfWork(_database)

    resolved_authorizer = authorizer or _build_authorizer(resolved)
    resolved_transports = transports or _build_transports(resolved)
    resolved_templates = templates if templates is not None else dict(DEFAULT_TEMPLATES)

    app = FastAPI(
        title="HUDHUD Notification",
        version=__version__,
        description=(
            "Journey notifications, channel fan-out, per-channel preferences "
            "and the notification centre"
        ),
    )
    app.include_router(health_router)
    app.include_router(notification_router)

    app.state.settings = resolved
    app.state.engine = engine
    # A *factory*, never a unit of work. A unit of work holds one request's session and
    # one request's pending writes; sharing the instance meant concurrent requests fought
    # over one session — measured against PostgreSQL, 1 of 40 succeeded. The application
    # services are built per request too, because each holds a reference to it.
    # `tests/architecture/test_request_scoped_state.py` keeps it that way.
    app.state.unit_of_work_factory = unit_of_work_factory
    app.state.authorizer = resolved_authorizer
    app.state.transports = resolved_transports
    app.state.templates = resolved_templates
    app.state.readiness_report = evaluate_readiness(
        persistence_wired=persistence_wired,
        authorization_configured=resolved_authorizer.is_production_ready,
        extra_checks={
            "recipient_hash_key_configured": bool(resolved.recipient_hash_key),
            # NTF-03 — the receiver always gets an SMS. Without an SMS transport this
            # service cannot keep that promise, so it says so rather than appearing ready.
            "sms_transport_configured": resolved_transports[
                Channel.SMS
            ].is_production_ready,
            "templates_loaded": bool(resolved_templates),
        },
    )
    return app


def _build_authorizer(settings: NotificationSettings) -> NotificationAuthorizer:
    if not settings.identity_authorization_enabled:
        return DefaultDenyNotificationAuthorizer()
    assert settings.identity_base_url is not None
    assert settings.identity_service_credential is not None
    return IdentityNotificationAuthorizer(
        base_url=settings.identity_base_url,
        service_credential=settings.identity_service_credential,
    )


def _build_transports(
    settings: NotificationSettings,
) -> dict[Channel, ChannelTransport]:
    """Every channel gets a transport; unconfigured ones refuse rather than pretend.

    Recording a message as sent when no provider exists is the failure that costs a
    receiver their parcel, so the default is an explicit refusal.
    """
    _ = settings.enabled_channels
    return {channel: UnavailableTransport(channel) for channel in Channel}


app = create_app()
