"""Service-to-service credential boundary for token introspection.

Introspection tells the caller who a bearer token belongs to. That answer is only safe to
give to another platform service, never to an end user, so the caller must present a
service credential of its own (ADR-0004: forwarded identity headers are not proof).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ServiceCaller:
    service_name: str


class ServiceCredentialVerifier(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    def verify(self, credential: str) -> ServiceCaller | None:
        """Return the calling service, or None when the credential is not accepted."""
