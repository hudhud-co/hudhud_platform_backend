"""Test helpers for JWT/JWKS authorization."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tracking.infrastructure.jwt.jwks_client import JwksKey, JwksUnavailableError
from tracking.ports.query_authorizer import TrackingAccessDecision

TEST_ISSUER = "https://identity.example.test"
TEST_AUDIENCE = "tracking"
TEST_KID = "test-key-1"
TEST_SUBJECT = UUID("11111111-1111-4111-8111-111111111111")
SENSITIVE_TOKEN_MARKER = "super-secret-jwt-value-do-not-leak"


@dataclass
class TestSigningMaterial:
    private_key_pem: bytes
    public_jwk: dict[str, Any]
    kid: str = TEST_KID


def generate_signing_material(*, kid: str = TEST_KID) -> TestSigningMaterial:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()
    public_jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": jwt.utils.base64url_encode(public_numbers.n.to_bytes(256, "big")).decode("ascii"),
        "e": jwt.utils.base64url_encode(public_numbers.e.to_bytes(3, "big")).decode("ascii"),
    }
    return TestSigningMaterial(private_key_pem=private_key_pem, public_jwk=public_jwk, kid=kid)


def mint_access_token(
    material: TestSigningMaterial,
    *,
    issuer: str = TEST_ISSUER,
    audience: str = TEST_AUDIENCE,
    subject: UUID = TEST_SUBJECT,
    algorithm: str = "RS256",
    kid: str | None = None,
    expires_in_seconds: int = 300,
    not_before_offset_seconds: int | None = None,
    extra_headers: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    if not_before_offset_seconds is not None:
        payload["nbf"] = now + not_before_offset_seconds
    headers = {"kid": kid or material.kid, "alg": algorithm}
    if extra_headers:
        headers.update(extra_headers)
    return jwt.encode(
        payload,
        material.private_key_pem,
        algorithm=algorithm,
        headers=headers,
    )


@dataclass
class FakeJwksClient:
    """In-memory JWKS client for tests — no network."""

    keys_by_kid: dict[str, JwksKey]
    available: bool = True
    refresh_calls: int = 0
    fail_refresh: bool = False

    async def get_signing_key(self, kid: str) -> JwksKey:
        if not self.available:
            raise JwksUnavailableError("jwks unavailable")
        if kid in self.keys_by_kid:
            return self.keys_by_kid[kid]
        self.refresh_calls += 1
        if self.fail_refresh:
            raise JwksUnavailableError("jwks refresh failed")
        if kid in self.keys_by_kid:
            return self.keys_by_kid[kid]
        raise KeyError("unknown signing key kid")

    async def check_available(self) -> bool:
        return self.available and bool(self.keys_by_kid)


def jwks_key_from_material(material: TestSigningMaterial) -> JwksKey:
    return JwksKey(
        kid=material.kid,
        key=jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(material.public_jwk)),
    )


@dataclass
class AllowShipmentAccessPolicy:
    """Test policy that grants shipment reads for configured subjects."""

    allowed_subjects: set[UUID] = field(default_factory=set)
    production_ready: bool = True

    @property
    def is_production_ready(self) -> bool:
        return self.production_ready

    async def authorize_shipment_timeline_read(
        self,
        *,
        subject_id: UUID,
        shipment_id: UUID,
    ) -> TrackingAccessDecision:
        _ = shipment_id
        if subject_id in self.allowed_subjects:
            return TrackingAccessDecision.allow()
        return TrackingAccessDecision.forbidden()


@dataclass
class DenyShipmentAccessPolicy:
    """Test policy that always denies shipment reads."""

    production_ready: bool = False

    @property
    def is_production_ready(self) -> bool:
        return self.production_ready

    async def authorize_shipment_timeline_read(
        self,
        *,
        subject_id: UUID,
        shipment_id: UUID,
    ) -> TrackingAccessDecision:
        _ = subject_id, shipment_id
        return TrackingAccessDecision.forbidden()
