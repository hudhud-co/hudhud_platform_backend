"""Finance authorization adapters.

Finance never imports Identity. It asks over HTTP, and with no Identity configured it
denies everything — a finance service that authorizes by default is one that lets
anyone approve their own payout.

The introspection body also carries the merchant a principal speaks for, because a
merchant owner may read their own balance and request their own payout, and nobody
else's.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from finance.ports.authorization import (
    AuthorizerUnavailableError,
    FinanceAccessDecision,
    FinanceActor,
    FinanceCommand,
    FinanceRole,
)

#: Identity's role names on the left, this service's own vocabulary on the right.
#: The left-hand side is not a free choice: it is exactly what Identity puts in an
#: introspection response, published in `contracts/identity/roles.yaml`. A name that is
#: not there is silently dropped, and the caller authenticates with no permissions —
#: which is how `LAST_MILE_DRIVER` and `MERCHANT_OWNER` sat here for a whole wave while
#: Identity had only ever granted `DELIVERY_DRIVER` and `MERCHANT_MEMBER`. A boundary
#: test now compares these keys against that contract.
_ROLE_MAP = {
    "DELIVERY_DRIVER": FinanceRole.LAST_MILE_DRIVER,
    "PICKUP_DRIVER": FinanceRole.PICKUP_DRIVER,
    "MERCHANT_MEMBER": FinanceRole.MERCHANT_OWNER,
    # ADR-0012 separates these two deliberately: a cashier takes cash at a hub, an
    # accountant verifies an exchange receipt, and neither is the other.
    "HUB_CASHIER": FinanceRole.HUB_CASHIER,
    "ACCOUNTANT": FinanceRole.ACCOUNTANT,
    "SUPPORT": FinanceRole.SUPPORT,
    "OPERATIONS": FinanceRole.OPERATIONS,
}
_REFUSED_STATUSES = frozenset({"SUSPENDED", "CLOSED"})


class DefaultDenyFinanceAuthorizer:
    """The composition default. A service with no proven identity source denies all."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: FinanceCommand,
        resource_id: UUID | None = None,
    ) -> FinanceAccessDecision:
        _ = (bearer_token, command, resource_id)
        return FinanceAccessDecision.unauthenticated()


class FakeFinanceAuthorizer:
    """Test-only authorizer resolving a token to a preset actor."""

    def __init__(
        self,
        *,
        token_actors: dict[str, FinanceActor] | None = None,
        denied_commands: frozenset[FinanceCommand] | None = None,
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
        command: FinanceCommand,
        resource_id: UUID | None = None,
    ) -> FinanceAccessDecision:
        _ = resource_id
        if self._unavailable:
            raise AuthorizerUnavailableError("identity unavailable")
        actor = self._token_actors.get(bearer_token)
        if actor is None:
            return FinanceAccessDecision.unauthenticated()
        if command in self._denied:
            return FinanceAccessDecision.forbidden()
        return FinanceAccessDecision.allow(actor)


class IdentityFinanceAuthorizer:
    """Resolve a bearer token into a Finance actor through Identity introspection."""

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
        command: FinanceCommand,
        resource_id: UUID | None = None,
    ) -> FinanceAccessDecision:
        _ = (command, resource_id)
        if not bearer_token.strip():
            return FinanceAccessDecision.unauthenticated()
        actor = build_actor(await self._introspect(bearer_token))
        if actor is None:
            return FinanceAccessDecision.unauthenticated()
        return FinanceAccessDecision.allow(actor)

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
        # 403 means Finance's own service credential was rejected, and 5xx is Identity
        # failing. Neither is a statement about the caller, so neither may deny them.
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"identity introspection returned {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise AuthorizerUnavailableError("introspection body was not an object")
        return body


def build_actor(body: dict[str, object]) -> FinanceActor | None:
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
    return FinanceActor(
        principal_id=parsed,
        roles=frozenset(roles),
        merchant_id=_merchant_scope(body),
    )


def _merchant_scope(body: dict[str, object]) -> UUID | None:
    """Which merchant this principal speaks for, from Identity's scoped grants.

    Identity does not publish a top-level ``merchant_id``: it returns `grants`, each with
    a `scope_kind` and an opaque `scope_id` that belongs to another context. A merchant
    owner is a principal holding a `MERCHANT`-scoped grant, and the scope id is the
    merchant.

    Two grants for two different merchants means the scope is ambiguous, so this returns
    nothing and the merchant-scoped reads are refused. Guessing which of them the caller
    meant would hand one merchant another's balance.
    """
    scoped: set[UUID] = set()
    for grant in body.get("grants", []):  # type: ignore[union-attr]
        if not isinstance(grant, dict):
            continue
        if grant.get("scope_kind") != "MERCHANT":
            continue
        raw = grant.get("scope_id")
        if not isinstance(raw, str):
            continue
        try:
            scoped.add(UUID(raw))
        except ValueError:
            # An unparseable scope is no scope. Refusing is safer than attaching the
            # actor to the wrong merchant's money.
            continue
    if len(scoped) == 1:
        return next(iter(scoped))
    return None
