"""Merchant authorization adapters.

Same shape as Pickup's and Customer's: default-deny until Identity is configured, then a
real introspection-backed adapter. Merchant never imports Identity — it asks over HTTP.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from merchant.ports.authorization import (
    AuthorizerUnavailableError,
    MerchantAccessDecision,
    MerchantActor,
    MerchantCommand,
    MerchantRole,
)

_ROLE_MAP = {
    "CUSTOMER": MerchantRole.CUSTOMER,
    "MERCHANT_OWNER": MerchantRole.MERCHANT_OWNER,
    "MERCHANT_MEMBER": MerchantRole.MERCHANT_MEMBER,
    "SUPPORT": MerchantRole.SUPPORT,
    "OPERATIONS": MerchantRole.OPERATIONS,
}
_REFUSED_STATUSES = frozenset({"SUSPENDED", "CLOSED"})


class DefaultDenyMerchantAuthorizer:
    """The composition default. A service with no proven identity source denies all."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: MerchantCommand,
        resource_id: UUID | None = None,
    ) -> MerchantAccessDecision:
        _ = (bearer_token, command, resource_id)
        return MerchantAccessDecision.unauthenticated()


class FakeMerchantAuthorizer:
    """Test-only authorizer resolving a token to a preset actor."""

    def __init__(
        self,
        *,
        token_actors: dict[str, MerchantActor] | None = None,
        denied_commands: frozenset[MerchantCommand] | None = None,
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
        command: MerchantCommand,
        resource_id: UUID | None = None,
    ) -> MerchantAccessDecision:
        _ = resource_id
        if self._unavailable:
            raise AuthorizerUnavailableError("identity unavailable")
        actor = self._token_actors.get(bearer_token)
        if actor is None:
            return MerchantAccessDecision.unauthenticated()
        if command in self._denied:
            return MerchantAccessDecision.forbidden()
        return MerchantAccessDecision.allow(actor)


class IdentityMerchantAuthorizer:
    """Resolve a bearer token into a Merchant actor through Identity introspection."""

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
        command: MerchantCommand,
        resource_id: UUID | None = None,
    ) -> MerchantAccessDecision:
        _ = (command, resource_id)
        if not bearer_token.strip():
            return MerchantAccessDecision.unauthenticated()
        actor = build_actor(await self._introspect(bearer_token))
        if actor is None:
            return MerchantAccessDecision.unauthenticated()
        return MerchantAccessDecision.allow(actor)

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
        # 403 means Merchant's own service credential was rejected, and 5xx is Identity
        # failing. Neither is a statement about the caller, so neither may deny them.
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"identity introspection returned {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise AuthorizerUnavailableError("introspection body was not an object")
        return body


def build_actor(body: dict[str, object]) -> MerchantActor | None:
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
    return MerchantActor(
        principal_id=parsed,
        roles=frozenset(roles),
        merchant_ids=_merchant_scopes(body),
    )


def _merchant_scopes(body: dict[str, object]) -> frozenset[UUID]:
    """Which merchants this principal owns, from Identity's scoped grants.

    Identity does not publish a top-level ``merchant_ids`` list: it returns `grants`,
    each with a `scope_kind` and an opaque `scope_id` belonging to another context. A
    merchant owner is a principal holding a `MERCHANT`-scoped grant, and the scope id is
    the merchant.

    Every scope is kept, because one person may own several stores (Customer App v3
    `yourStores`). An unparseable scope is dropped rather than guessed: attaching an
    actor to the wrong merchant would hand them another owner's store.
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
            continue
    return frozenset(scoped)
