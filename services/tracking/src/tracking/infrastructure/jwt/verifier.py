"""Fail-closed JWT verification using JWKS-resolved signing keys."""

from __future__ import annotations

from uuid import UUID

import jwt

from tracking.infrastructure.jwt.jwks_client import JwksClient, JwksUnavailableError
from tracking.ports.query_authorizer import AuthorizerUnavailableError

_FORBIDDEN_ALGORITHMS = frozenset({"none"})
_SYMMETRIC_PREFIXES = ("HS",)


class JwtAuthenticationError(Exception):
    """JWT failed verification — maps to unauthenticated at the authorizer boundary."""


class JwtVerifier:
    """Verify bearer JWTs against issuer, audience, algorithm allowlist, and JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        allowed_algorithms: tuple[str, ...],
        jwks_client: JwksClient,
    ) -> None:
        if not allowed_algorithms:
            msg = "jwt allowed algorithms must not be empty"
            raise ValueError(msg)
        normalized = tuple(alg.upper() for alg in allowed_algorithms)
        for alg in normalized:
            if alg == "NONE" or alg in _FORBIDDEN_ALGORITHMS or alg.startswith(_SYMMETRIC_PREFIXES):
                msg = f"jwt algorithm not permitted: {alg}"
                raise ValueError(msg)
        self._issuer = issuer
        self._audience = audience
        self._allowed_algorithms = normalized
        self._jwks_client = jwks_client

    @property
    def is_configured(self) -> bool:
        return bool(self._issuer and self._audience and self._allowed_algorithms)

    async def verify_subject(self, token: str) -> UUID:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise JwtAuthenticationError("invalid token header") from exc

        algorithm = header.get("alg")
        if not isinstance(algorithm, str):
            raise JwtAuthenticationError("missing algorithm")
        algorithm_upper = algorithm.upper()
        if algorithm_upper == "NONE" or algorithm_upper in _FORBIDDEN_ALGORITHMS:
            raise JwtAuthenticationError("algorithm not allowed")
        if algorithm_upper.startswith(_SYMMETRIC_PREFIXES):
            raise JwtAuthenticationError("algorithm not allowed")
        if algorithm_upper not in self._allowed_algorithms:
            raise JwtAuthenticationError("algorithm not allowed")

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise JwtAuthenticationError("missing kid")

        try:
            signing_key = await self._jwks_client.get_signing_key(kid)
        except KeyError as exc:
            raise JwtAuthenticationError("unknown signing key") from exc
        except JwksUnavailableError:
            raise
        except AuthorizerUnavailableError:
            raise

        try:
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm_upper],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["exp", "sub"],
                    "verify_exp": True,
                    "verify_nbf": True,
                },
            )
        except jwt.PyJWTError as exc:
            raise JwtAuthenticationError("token verification failed") from exc

        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise JwtAuthenticationError("missing subject")
        try:
            return UUID(subject)
        except ValueError as exc:
            raise JwtAuthenticationError("invalid subject") from exc
