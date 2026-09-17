"""Customer authorization adapters.

Same shape as Pickup's: default-deny until Identity is configured, then a real
introspection-backed adapter. Customer never imports Identity.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from customer.ports.authorization import (
    AuthorizerUnavailableError,
    CustomerAccessDecision,
    CustomerActor,
    CustomerCommand,
    CustomerRole,
)

_ROLE_MAP = {
    "CUSTOMER": CustomerRole.CUSTOMER,
    "MERCHANT_MEMBER": CustomerRole.MERCHANT_MEMBER,
    "SUPPORT": CustomerRole.SUPPORT,
    "OPERATIONS": CustomerRole.OPERATIONS,
}
_REFUSED_STATUSES = frozenset({"SUSPENDED", "CLOSED"})


class DefaultDenyCustomerAuthorizer:
    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: CustomerCommand,
        resource_id: UUID | None = None,
    ) -> CustomerAccessDecision:
        _ = (bearer_token, command, resource_id)
        return CustomerAccessDecision.unauthenticated()


class FakeCustomerAuthorizer:
    """Test-only authorizer resolving a token to a preset actor."""

    def __init__(
        self,
        *,
        token_actors: dict[str, CustomerActor] | None = None,
        denied_commands: frozenset[CustomerCommand] | None = None,
        production_ready: bool = True,
    ) -> None:
        self._token_actors = token_actors or {}
        self._denied = denied_commands or frozenset()
        self._production_ready = production_ready

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: CustomerCommand,
        resource_id: UUID | None = None,
    ) -> CustomerAccessDecision:
        _ = resource_id
        if command in self._denied:
            return CustomerAccessDecision.forbidden()
        actor = self._token_actors.get(bearer_token)
        if actor is None:
            return CustomerAccessDecision.unauthenticated()
        return CustomerAccessDecision.allow(actor)


class IdentityCustomerAuthorizer:
    """Resolve a bearer token into a Customer actor through Identity introspection."""

    def __init__(
        self,
        *,
        base_url: str,
        service_credential: str,
        timeout_seconds: float = 3.0,
        client_factory=None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_credential = service_credential
        self._timeout = timeout_seconds
        self._client_factory = client_factory

    @property
    def is_production_ready(self) -> bool:
        return True

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: CustomerCommand,
        resource_id: UUID | None = None,
    ) -> CustomerAccessDecision:
        _ = (command, resource_id)
        if not bearer_token.strip():
            return CustomerAccessDecision.unauthenticated()
        body = await self._introspect(bearer_token)
        actor = build_actor(body)
        if actor is None:
            return CustomerAccessDecision.unauthenticated()
        return CustomerAccessDecision.allow(actor)

    async def _introspect(self, token: str) -> dict[str, object]:
        client_factory = self._client_factory
        if client_factory is None:

            def client_factory() -> httpx.AsyncClient:
                return httpx.AsyncClient(timeout=self._timeout)

        try:
            async with client_factory() as client:
                response = await client.post(
                    f"{self._base_url}/identity/tokens/introspect",
                    json={"token": token},
                    headers={"X-Service-Credential": self._service_credential},
                )
        except Exception as exc:  # noqa: BLE001 - any transport fault is unavailability
            raise AuthorizerUnavailableError("identity introspection failed") from exc
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"identity introspection returned {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise AuthorizerUnavailableError("introspection body was not an object")
        return body


def build_actor(body: dict[str, object]) -> CustomerActor | None:
    if not isinstance(body, dict) or body.get("active") is not True:
        return None
    principal_id = body.get("principal_id")
    if not isinstance(principal_id, str):
        return None
    status = body.get("status")
    if isinstance(status, str) and status.upper() in _REFUSED_STATUSES:
        return None
    try:
        parsed = UUID(principal_id)
    except ValueError:
        return None
    roles = {
        _ROLE_MAP[name]
        for name in body.get("roles", [])  # type: ignore[union-attr]
        if isinstance(name, str) and name in _ROLE_MAP
    }
    return CustomerActor(principal_id=parsed, roles=frozenset(roles))
