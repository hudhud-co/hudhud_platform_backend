"""Asking Merchant what a principal may do inside a merchant.

Ordering needs one fact Merchant owns: is this principal an owner or an active team
member of that merchant, and what may they do there? It asks over HTTP and never reads
Merchant's tables or imports its package.

The default is a fail-closed adapter that reports no access at all, so a deployment that
forgets to configure Merchant refuses merchant work rather than trusting the token's own
claim about which merchants the caller belongs to.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from ordering.ports.authorization import AuthorizerUnavailableError, StoreAccess


class NoMerchantAccess:
    """The composition default: no configured Merchant means no merchant reach."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def store_access(self, principal_id: UUID) -> tuple[StoreAccess, ...]:
        _ = principal_id
        return ()


class FakeMerchantAccess:
    """Test-only adapter returning preset access."""

    def __init__(
        self,
        *,
        access: dict[UUID, tuple[StoreAccess, ...]] | None = None,
        unavailable: bool = False,
        production_ready: bool = True,
    ) -> None:
        self._access = access or {}
        self._unavailable = unavailable
        self._production_ready = production_ready

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    async def store_access(self, principal_id: UUID) -> tuple[StoreAccess, ...]:
        if self._unavailable:
            raise AuthorizerUnavailableError("merchant unavailable")
        return self._access.get(principal_id, ())


class HttpMerchantAccess:
    """Read a principal's store access from Merchant's own read model."""

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

    async def store_access(self, principal_id: UUID) -> tuple[StoreAccess, ...]:
        client_factory = self._client_factory
        if client_factory is None:

            def client_factory() -> httpx.AsyncClient:
                return httpx.AsyncClient(timeout=self._timeout)

        try:
            async with client_factory() as client:
                response = await client.get(
                    f"{self._base_url}/merchant/principals/{principal_id}/store-access",
                    headers={"Authorization": f"Bearer {self._service_credential}"},
                )
        except Exception as exc:  # noqa: BLE001 - any transport fault is unavailability
            raise AuthorizerUnavailableError("merchant store-access failed") from exc
        # A 403 means Ordering's own credential was rejected and a 5xx means Merchant is
        # down. Neither says anything about the caller, so neither may deny them.
        if response.status_code != 200:
            raise AuthorizerUnavailableError(
                f"merchant store-access returned {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, list):
            raise AuthorizerUnavailableError("store-access body was not a list")
        return tuple(
            access for access in (_parse(row) for row in body) if access is not None
        )


def _parse(row: object) -> StoreAccess | None:
    if not isinstance(row, dict):
        return None
    try:
        merchant_id = UUID(str(row["merchant_id"]))
    except (KeyError, ValueError):
        return None
    store_ids = []
    for raw in row.get("store_ids", []):
        try:
            store_ids.append(UUID(str(raw)))
        except ValueError:
            continue
    permissions = {
        str(item) for item in row.get("permissions", []) if isinstance(item, str)
    }
    return StoreAccess(
        merchant_id=merchant_id,
        role=str(row.get("role", "")),
        permissions=frozenset(permissions),
        store_ids=tuple(store_ids),
    )
