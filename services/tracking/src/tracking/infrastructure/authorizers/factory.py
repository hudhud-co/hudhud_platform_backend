"""Composition helpers for query authorization."""

from __future__ import annotations

import asyncio

from tracking.config import TrackingSettings
from tracking.infrastructure.authorizers.default_deny import DefaultDenyQueryAuthorizer
from tracking.infrastructure.authorizers.jwt_query_authorizer import JwtQueryAuthorizer
from tracking.infrastructure.jwt.jwks_client import HttpJwksClient, JwksClient
from tracking.infrastructure.jwt.verifier import JwtVerifier
from tracking.infrastructure.policies.default_deny import DefaultDenyShipmentAccessPolicy
from tracking.ports.query_authorizer import TrackingQueryAuthorizer
from tracking.ports.shipment_access import ShipmentAccessPolicy


def _authorizer_readiness_flags(
    authorizer: TrackingQueryAuthorizer,
) -> tuple[bool, bool, bool, bool]:
    jwt_verifier_configured = bool(getattr(authorizer, "jwt_verifier_configured", False))
    jwks_dependency_available = bool(getattr(authorizer, "jwks_dependency_available", False))
    shipment_access_policy_configured = bool(
        getattr(authorizer, "shipment_access_policy_configured", False)
    )
    query_authorizer_configured = authorizer.is_production_ready
    return (
        query_authorizer_configured,
        jwt_verifier_configured,
        jwks_dependency_available,
        shipment_access_policy_configured,
    )


def build_query_authorizer(
    settings: TrackingSettings,
    *,
    jwks_client: JwksClient | None = None,
    shipment_policy: ShipmentAccessPolicy | None = None,
    jwks_available: bool | None = None,
) -> TrackingQueryAuthorizer:
    """Build the timeline query authorizer from service settings."""
    if not settings.jwt.is_complete():
        return DefaultDenyQueryAuthorizer()

    client = jwks_client or HttpJwksClient(
        settings.jwt.jwks_url or "",
        timeout_seconds=settings.jwt.jwks_timeout_seconds,
        cache_ttl_seconds=settings.jwt.jwks_cache_ttl_seconds,
    )
    verifier = JwtVerifier(
        issuer=settings.jwt.issuer or "",
        audience=settings.jwt.audience or "",
        allowed_algorithms=settings.jwt.allowed_algorithms,
        jwks_client=client,
    )
    policy = shipment_policy or DefaultDenyShipmentAccessPolicy()

    resolved_jwks_available = jwks_available
    if resolved_jwks_available is None:
        try:
            resolved_jwks_available = asyncio.run(client.check_available())
        except RuntimeError:
            resolved_jwks_available = False

    return JwtQueryAuthorizer(
        verifier=verifier,
        shipment_policy=policy,
        jwks_available=bool(resolved_jwks_available),
    )


__all__ = ["_authorizer_readiness_flags", "build_query_authorizer"]
