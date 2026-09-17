"""Shared builders for Identity tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from identity.application.authentication_service import (
    AuthenticationService,
    OtpPolicy,
    RequestOtpCommand,
    VerifyOtpCommand,
)
from identity.application.introspection_service import IntrospectionService
from identity.application.role_service import RoleService
from identity.infrastructure.adapters import (
    RecordingOtpDelivery,
    SharedSecretServiceCredentials,
)
from identity.infrastructure.memory import InMemoryIdentityUnitOfWork

SIGNING_KEY = "identity-unit-test-signing-key-32ch"
SERVICE_CREDENTIAL = "pickup-service-credential-value-32"  # noqa: S105 - test fixture
BASE_TIME = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
PHONE = "+9647701820934"
OTHER_PHONE = "+9647701820935"


def minutes(count: int) -> timedelta:
    return timedelta(minutes=count)


def build_store() -> InMemoryIdentityUnitOfWork:
    return InMemoryIdentityUnitOfWork()


def build_delivery() -> RecordingOtpDelivery:
    return RecordingOtpDelivery()


def credentials() -> SharedSecretServiceCredentials:
    return SharedSecretServiceCredentials({"pickup": SERVICE_CREDENTIAL})


def auth_service(
    store: InMemoryIdentityUnitOfWork,
    delivery: RecordingOtpDelivery,
    *,
    policy: OtpPolicy | None = None,
) -> AuthenticationService:
    return AuthenticationService(
        store,
        signing_key=SIGNING_KEY,
        otp_delivery=delivery,
        policy=policy or OtpPolicy(),
    )


def introspection_service(store: InMemoryIdentityUnitOfWork) -> IntrospectionService:
    return IntrospectionService(
        store, signing_key=SIGNING_KEY, service_credentials=credentials()
    )


def role_service(store: InMemoryIdentityUnitOfWork) -> RoleService:
    return RoleService(store)


def sign_in(
    store: InMemoryIdentityUnitOfWork,
    delivery: RecordingOtpDelivery,
    *,
    phone: str = PHONE,
    at: datetime | None = None,
    device_id: str | None = None,
):
    """Complete a full OTP exchange and return the resulting session."""
    service = auth_service(store, delivery)
    now = at or BASE_TIME
    issued = service.request_code(RequestOtpCommand(phone=phone, occurred_at=now))
    code = delivery.code_for(str(issued.challenge_id))
    return service.verify_code(
        VerifyOtpCommand(
            challenge_id=issued.challenge_id,
            code=code,
            occurred_at=now + minutes(1),
            device_id=device_id,
        )
    )
