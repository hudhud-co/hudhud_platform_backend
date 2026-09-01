"""JWKS fetch with bounded timeout, cache TTL, and controlled refresh."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt

from tracking.ports.query_authorizer import AuthorizerUnavailableError


@dataclass(frozen=True, slots=True)
class JwksKey:
    """Signing key material resolved from JWKS."""

    kid: str
    key: Any


class JwksClient(Protocol):
    """Port for resolving JWT signing keys from JWKS."""

    async def get_signing_key(self, kid: str) -> JwksKey: ...

    async def check_available(self) -> bool: ...


class JwksUnavailableError(AuthorizerUnavailableError):
    """JWKS dependency unavailable — HTTP adapter maps to 503."""


def _parse_jwks_document(document: dict[str, Any]) -> dict[str, JwksKey]:
    keys: dict[str, JwksKey] = {}
    for entry in document.get("keys", []):
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or not kid:
            continue
        keys[kid] = JwksKey(kid=kid, key=jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(entry)))
    return keys


class HttpJwksClient:
    """HTTP JWKS client with cache, timeout, and one refresh on unknown kid."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        cache_ttl_seconds: int,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._http = http_client or httpx.Client(timeout=timeout_seconds)
        self._keys: dict[str, JwksKey] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    def _cache_valid(self) -> bool:
        if self._fetched_at is None:
            return False
        return (time.monotonic() - self._fetched_at) < self._cache_ttl_seconds

    def _fetch_jwks_sync(self) -> dict[str, JwksKey]:
        try:
            response = self._http.get(self._url)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise JwksUnavailableError("jwks fetch failed") from exc
        if not isinstance(document, dict):
            raise JwksUnavailableError("jwks document invalid")
        keys = _parse_jwks_document(document)
        if not keys:
            raise JwksUnavailableError("jwks document has no usable keys")
        return keys

    async def _refresh_cache(self, *, force: bool = False) -> None:
        if not force and self._cache_valid():
            return
        keys = await asyncio.to_thread(self._fetch_jwks_sync)
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def get_signing_key(self, kid: str) -> JwksKey:
        async with self._lock:
            if not self._cache_valid():
                await self._refresh_cache(force=True)

            if kid in self._keys:
                return self._keys[kid]

            await self._refresh_cache(force=True)
            if kid in self._keys:
                return self._keys[kid]

        raise KeyError("unknown signing key kid")

    async def check_available(self) -> bool:
        try:
            async with self._lock:
                await self._refresh_cache(force=True)
            return bool(self._keys)
        except JwksUnavailableError:
            return False
