"""HTTP transport for Identity token introspection.

Kept separate from the authorizer so the mapping logic can be tested without a socket and
the socket logic can be swapped without touching the mapping.
"""

from __future__ import annotations

from typing import Any

import httpx

from pickup.ports.recovery_authorizer import AuthorizerUnavailableError


class HttpIntrospectionTransport:
    """Call ``POST {base_url}/identity/tokens/introspect`` with the service credential."""

    def __init__(
        self,
        *,
        base_url: str,
        service_credential: str,
        timeout_seconds: float = 3.0,
        client_factory: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_credential = service_credential
        self._timeout = timeout_seconds
        self._client_factory = client_factory

    async def introspect(self, *, token: str) -> dict[str, object]:
        client_factory = self._client_factory
        if client_factory is None:

            def client_factory() -> httpx.AsyncClient:
                return httpx.AsyncClient(timeout=self._timeout)

        async with client_factory() as client:
            response = await client.post(
                f"{self._base_url}/identity/tokens/introspect",
                json={"token": token},
                headers={"X-Service-Credential": self._service_credential},
            )
        if response.status_code == 403:
            # Pickup's own credential was rejected: an operational fault, not a user
            # denial. Surfacing it as "unauthenticated" would blame the wrong party.
            raise AuthorizerUnavailableError("identity rejected the service credential")
        if response.status_code >= 500:
            raise AuthorizerUnavailableError(
                f"identity introspection returned {response.status_code}"
            )
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"unexpected introspection status {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise AuthorizerUnavailableError("introspection body was not an object")
        return body
