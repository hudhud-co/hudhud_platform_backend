"""Hub authorization adapters.

Same shape as Pickup's and Customer's: default-deny until Identity is configured, then a
real introspection-backed adapter. Hub never imports Identity — it asks over HTTP.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from hub.ports.authorization import (
    AuthorizerUnavailableError,
    HubAccessDecision,
    HubActor,
    HubCommand,
    HubRole,
)

_ROLE_MAP = {
    "HUB_OPERATOR": HubRole.HUB_OPERATOR,
    "LINEHAUL_DRIVER": HubRole.LINEHAUL_DRIVER,
    "LAST_MILE_DRIVER": HubRole.LAST_MILE_DRIVER,
    "SUPPORT": HubRole.SUPPORT,
    "OPERATIONS": HubRole.OPERATIONS,
}
_REFUSED_STATUSES = frozenset({"SUSPENDED", "CLOSED"})


class DefaultDenyHubAuthorizer:
    """The composition default. A service with no proven identity source denies all."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: HubCommand,
        resource_id: UUID | None = None,
    ) -> HubAccessDecision:
        _ = (bearer_token, command, resource_id)
        return HubAccessDecision.unauthenticated()


class FakeHubAuthorizer:
    """Test-only authorizer resolving a token to a preset actor."""

    def __init__(
        self,
        *,
        token_actors: dict[str, HubActor] | None = None,
        denied_commands: frozenset[HubCommand] | None = None,
        production_ready: bool = True,
        unavailable: bool = False,
    ) -> None:
        self._token_actors = token_actors or {}
        self._denied = denied_commands or frozenset()
        self._production_ready = production_ready
        self._unavailable = unavailable

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: HubCommand,
        resource_id: UUID | None = None,
    ) -> HubAccessDecision:
        _ = resource_id
        if self._unavailable:
            raise AuthorizerUnavailableError("identity unavailable")
        actor = self._token_actors.get(bearer_token)
        if actor is None:
            return HubAccessDecision.unauthenticated()
        if command in self._denied:
            return HubAccessDecision.forbidden()
        return HubAccessDecision.allow(actor)


class IdentityHubAuthorizer:
    """Resolve a bearer token into a Hub actor through Identity introspection."""

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
        command: HubCommand,
        resource_id: UUID | None = None,
    ) -> HubAccessDecision:
        _ = (command, resource_id)
        if not bearer_token.strip():
            return HubAccessDecision.unauthenticated()
        actor = build_actor(await self._introspect(bearer_token))
        if actor is None:
            return HubAccessDecision.unauthenticated()
        return HubAccessDecision.allow(actor)

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
        # 403 means Hub's own service credential was rejected, and 5xx is Identity
        # failing. Neither is a statement about the caller, so neither may deny them.
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"identity introspection returned {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise AuthorizerUnavailableError("introspection body was not an object")
        return body


def build_actor(body: dict[str, object]) -> HubActor | None:
    """Translate an introspection body into an actor, dropping anything unrecognised."""
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
    hub_ids = set()
    for raw in body.get("hub_ids", []):  # type: ignore[union-attr]
        if not isinstance(raw, str):
            continue
        try:
            hub_ids.add(UUID(raw))
        except ValueError:
            continue
    return HubActor(
        principal_id=parsed,
        roles=frozenset(roles),
        hub_ids=frozenset(hub_ids),
    )
