"""JWT/JWKS-backed timeline query authorizer."""

from __future__ import annotations

from uuid import UUID

from tracking.infrastructure.jwt.jwks_client import JwksUnavailableError
from tracking.infrastructure.jwt.verifier import JwtAuthenticationError, JwtVerifier
from tracking.ports.query_authorizer import (
    AuthorizerUnavailableError,
    TrackingAccessDecision,
)
from tracking.ports.shipment_access import ShipmentAccessPolicy


class JwtQueryAuthorizer:
    """Authenticate via JWT/JWKS, then authorize shipment access via service policy."""

    def __init__(
        self,
        *,
        verifier: JwtVerifier,
        shipment_policy: ShipmentAccessPolicy,
        jwks_available: bool,
    ) -> None:
        self._verifier = verifier
        self._shipment_policy = shipment_policy
        self._jwks_available = jwks_available

    @property
    def is_production_ready(self) -> bool:
        return (
            self.jwt_verifier_configured
            and self.jwks_dependency_available
            and self.shipment_access_policy_configured
        )

    @property
    def jwt_verifier_configured(self) -> bool:
        return self._verifier.is_configured

    @property
    def jwks_dependency_available(self) -> bool:
        return self._jwks_available

    @property
    def shipment_access_policy_configured(self) -> bool:
        return self._shipment_policy.is_production_ready

    async def authorize_timeline_read(
        self,
        *,
        bearer_token: str,
        shipment_id: UUID,
    ) -> TrackingAccessDecision:
        if not bearer_token:
            return TrackingAccessDecision.unauthenticated()

        try:
            subject_id = await self._verifier.verify_subject(bearer_token)
        except JwtAuthenticationError:
            return TrackingAccessDecision.unauthenticated()
        except JwksUnavailableError as exc:
            raise AuthorizerUnavailableError("jwks unavailable") from exc

        return await self._shipment_policy.authorize_shipment_timeline_read(
            subject_id=subject_id,
            shipment_id=shipment_id,
        )
