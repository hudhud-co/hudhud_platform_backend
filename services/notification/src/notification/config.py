"""Notification settings.

``recipient_hash_key`` is the one setting without which this service refuses to start in
production. Without it a recipient key would have to be either a plain hash of a phone
number — trivially reversed by enumerating the Iraqi numbering plan — or the number
itself. Neither is acceptable for a table that exists to remember who was messaged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ProductionStartupBlockedError(RuntimeError):
    """Production refused to start because a required gate is unset."""


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "notification"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    #: Keys the recipient digest. No default: see the module docstring.
    recipient_hash_key: str | None = None
    #: Delivery channels with a configured transport. Anything absent refuses to send.
    enabled_channels: tuple[str, ...] = field(default_factory=tuple)
    nats_url: str | None = None

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("NOTIFICATION_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("NOTIFICATION_IDENTITY_BASE_URL")
            missing.append("NOTIFICATION_IDENTITY_SERVICE_CREDENTIAL")
        if not self.recipient_hash_key:
            missing.append("NOTIFICATION_RECIPIENT_HASH_KEY")
        if missing:
            raise ProductionStartupBlockedError(
                "production startup blocked — unset gates: " + ", ".join(sorted(missing))
            )


def _parse_list(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple, frozenset, set)):
        items = [str(item).strip().upper() for item in raw]
    else:
        items = [part.strip().upper() for part in str(raw).split(",")]
    return tuple(item for item in items if item)


_MISSING = object()


def _resolve(overrides: dict, key: str, env_name: str, default=None):
    value = overrides.get(key, _MISSING)
    if value is not _MISSING:
        return value
    return os.environ.get(env_name, default)


def load_settings(**overrides: object) -> NotificationSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("NOTIFICATION_ENVIRONMENT", "local")))
    )
    return NotificationSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "NOTIFICATION_SERVICE_NAME", "notification")
        ),
        database_url=_resolve(overrides, "database_url", "NOTIFICATION_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "NOTIFICATION_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "NOTIFICATION_IDENTITY_SERVICE_CREDENTIAL",
        ),
        recipient_hash_key=_resolve(
            overrides, "recipient_hash_key", "NOTIFICATION_RECIPIENT_HASH_KEY"
        ),
        enabled_channels=_parse_list(
            _resolve(overrides, "enabled_channels", "NOTIFICATION_ENABLED_CHANNELS")
        ),
        nats_url=_resolve(overrides, "nats_url", "NOTIFICATION_NATS_URL"),
    )
