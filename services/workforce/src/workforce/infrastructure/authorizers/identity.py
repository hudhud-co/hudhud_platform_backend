"""Workforce authorization adapters.

Same shape as Pickup's and Customer's: default-deny until Identity is configured, then a
real introspection-backed adapter. Workforce never imports Identity — it asks over HTTP.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from workforce.ports.authorization import (
    AuthorizerUnavailableError,
    WorkforceAccessDecision,
    WorkforceActor,
    WorkforceCommand,
    WorkforceRole,
)

#: Identity's role names on the left, this service's own vocabulary on the right.
#: The left-hand side is not a free choice: it is exactly what Identity puts in an
#: introspection response, published in `contracts/identity/roles.yaml`. A name that is
#: not there is silently dropped, and the caller authenticates with no permissions —
#: which is how `LAST_MILE_DRIVER` and `MERCHANT_OWNER` sat here for a whole wave while
#: Identity had only ever granted `DELIVERY_DRIVER` and `MERCHANT_MEMBER`. A boundary
#: test now compares these keys against that contract.
_ROLE_MAP = {
    "PICKUP_DRIVER": WorkforceRole.PICKUP_DRIVER,
    "DELIVERY_DRIVER": WorkforceRole.LAST_MILE_DRIVER,
    "SUPPORT": WorkforceRole.SUPPORT,
    "OPERATIONS": WorkforceRole.OPERATIONS,
}

#: Roles this service distinguishes that Identity does not grant. `DRIVER_APPLICANT` is
#: someone who has applied and not yet been verified, and `LINEHAUL_DRIVER` is a
#: long-haul assignment — both are facts about the workforce, owned here, and neither is
#: a thing a token can carry. They stay in `WorkforceRole` and out of the map on purpose.
_NOT_AN_IDENTITY_ROLE = frozenset(
    {WorkforceRole.DRIVER_APPLICANT, WorkforceRole.LINEHAUL_DRIVER}
)
_REFUSED_STATUSES = frozenset({"SUSPENDED", "CLOSED"})


class DefaultDenyWorkforceAuthorizer:
    """The composition default. A service with no proven identity source denies all."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: WorkforceCommand,
        resource_id: UUID | None = None,
    ) -> WorkforceAccessDecision:
        _ = (bearer_token, command, resource_id)
        return WorkforceAccessDecision.unauthenticated()


class FakeWorkforceAuthorizer:
    """Test-only authorizer resolving a token to a preset actor."""

    def __init__(
        self,
        *,
        token_actors: dict[str, WorkforceActor] | None = None,
        denied_commands: frozenset[WorkforceCommand] | None = None,
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
        command: WorkforceCommand,
        resource_id: UUID | None = None,
    ) -> WorkforceAccessDecision:
        _ = resource_id
        if self._unavailable:
            raise AuthorizerUnavailableError("identity unavailable")
        actor = self._token_actors.get(bearer_token)
        if actor is None:
            return WorkforceAccessDecision.unauthenticated()
        if command in self._denied:
            return WorkforceAccessDecision.forbidden()
        return WorkforceAccessDecision.allow(actor)


class IdentityWorkforceAuthorizer:
    """Resolve a bearer token into a Workforce actor through Identity introspection."""

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
        command: WorkforceCommand,
        resource_id: UUID | None = None,
    ) -> WorkforceAccessDecision:
        _ = (command, resource_id)
        if not bearer_token.strip():
            return WorkforceAccessDecision.unauthenticated()
        actor = build_actor(await self._introspect(bearer_token))
        if actor is None:
            return WorkforceAccessDecision.unauthenticated()
        return WorkforceAccessDecision.allow(actor)

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
        # 403 means Workforce's own service credential was rejected, and 5xx is Identity
        # failing. Neither is a statement about the caller, so neither may deny them.
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"identity introspection returned {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise AuthorizerUnavailableError("introspection body was not an object")
        return body


def build_actor(body: dict[str, object]) -> WorkforceActor | None:
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
    # Workforce scopes nothing by facility: the questions it answers are about one
    # driver, and the answer is the same wherever it is asked.
    return WorkforceActor(principal_id=parsed, roles=frozenset(roles))
