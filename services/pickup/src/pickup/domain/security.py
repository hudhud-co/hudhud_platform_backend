"""Secret generation, constant-time verification, and stable fingerprints.

Nothing here persists or returns a raw secret: challenge secrets and offline tokens are
stored as keyed hashes only, and every comparison is constant time. The signing key is
supplied by configuration and never appears in a payload, log, or error message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

CHALLENGE_SECRET_BYTES = 32
CHALLENGE_PAYLOAD_SCHEME = "hudhud"
CHALLENGE_PAYLOAD_HOST = "pickup"
CHALLENGE_PAYLOAD_PATH = "verify"
CHALLENGE_PAYLOAD_VERSION = "1"
MIN_BARE_SECRET_LENGTH = 16

OFFLINE_TOKEN_TYPE = "pickup_offline_authorization"
OFFLINE_TOKEN_VERSION = 1


class InvalidOfflineToken(ValueError):
    """Offline authorization token is malformed, unsigned, or not ours."""


def generate_challenge_secret() -> str:
    """Cryptographically random single-use secret. Never persist or log the result."""
    return secrets.token_urlsafe(CHALLENGE_SECRET_BYTES)


def build_challenge_payload(*, secret: str) -> str:
    """Versioned deep link the driver app renders; carries the secret exactly once."""
    return (
        f"{CHALLENGE_PAYLOAD_SCHEME}://{CHALLENGE_PAYLOAD_HOST}/{CHALLENGE_PAYLOAD_PATH}"
        f"?v={CHALLENGE_PAYLOAD_VERSION}&s={secret}"
    )


def extract_challenge_secret(payload: str) -> str | None:
    """Parse a scanned payload; returns None when the payload is not ours."""
    raw = (payload or "").strip()
    if not raw:
        return None
    prefix = (
        f"{CHALLENGE_PAYLOAD_SCHEME}://{CHALLENGE_PAYLOAD_HOST}/{CHALLENGE_PAYLOAD_PATH}?"
    )
    if not raw.startswith(prefix):
        # Defensive tolerance for a bare secret typed instead of scanned.
        if "://" in raw or "=" in raw or "&" in raw:
            return None
        return raw if len(raw) >= MIN_BARE_SECRET_LENGTH else None
    query = raw[len(prefix) :]
    version: str | None = None
    secret: str | None = None
    for part in query.split("&"):
        key, sep, value = part.partition("=")
        if not sep or not value:
            continue
        if key == "v":
            version = value
        elif key == "s":
            secret = value
    if version != CHALLENGE_PAYLOAD_VERSION or not secret:
        return None
    return secret


def hash_challenge_secret(secret: str, *, challenge_id: UUID, signing_key: str) -> str:
    message = f"pickup_courier_challenge:{challenge_id}:{secret}"
    return hmac.new(
        signing_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_challenge_secret(
    secret: str,
    *,
    challenge_id: UUID,
    expected_hash: str,
    signing_key: str,
) -> bool:
    actual = hash_challenge_secret(secret, challenge_id=challenge_id, signing_key=signing_key)
    return hmac.compare_digest(actual, expected_hash)


def fingerprint_identifier(value: str) -> str:
    """Non-reversible fingerprint of a scanned identifier for audit binding."""
    return hashlib.sha256(f"pickup_identifier:{(value or '').strip()}".encode()).hexdigest()


def build_manifest_digest(*, shipment_id: UUID, scanned_identifier: str) -> str:
    """Canonical digest of the exact parcel identity a sender confirms."""
    material = f"v1|{shipment_id}|{fingerprint_identifier(scanned_identifier)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def assignment_fingerprint(
    *,
    pickup_task_id: UUID,
    assigned_driver_user_id: str,
    assigned_batch_id: UUID | None,
) -> str:
    batch = str(assigned_batch_id) if assigned_batch_id is not None else ""
    material = f"{pickup_task_id}|{assigned_driver_user_id}|{batch}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def assignment_revision(
    *,
    pickup_task_id: UUID,
    assigned_driver_user_id: str,
    assigned_batch_id: UUID | None,
    attempt_number: int,
    assignment_state: str,
    superseded_by_task_id: UUID | None,
) -> str:
    """Opaque server revision of one *assignment*.

    It covers who the work belongs to, not how far that work has progressed:
    reassignment, decline, a new attempt, or a supersede changes the revision, while
    ordinary forward progress does not. A driver captures a whole batch of commands
    offline under one downloaded revision, so folding progress into the revision would
    make every command after the first look stale.
    """
    material = "|".join(
        (
            "rev1",
            str(pickup_task_id),
            assigned_driver_user_id,
            str(assigned_batch_id) if assigned_batch_id is not None else "",
            str(attempt_number),
            assignment_state,
            str(superseded_by_task_id) if superseded_by_task_id is not None else "",
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def hash_device_id(device_id: str) -> str:
    """Device identifiers are stored as hashes only — never in clear text."""
    return hashlib.sha256(f"pickup_device:{device_id.strip()}".encode()).hexdigest()


def hash_offline_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def offline_event_fingerprint(
    *,
    operation_id: UUID,
    sequence: int,
    operation: str,
    resource_type: str,
    resource_id: UUID,
    assignment_revision_value: str,
    captured_at: datetime,
    payload: dict[str, Any],
) -> str:
    """Canonical fingerprint the client must reproduce for every captured command."""
    value = {
        "operation_id": str(operation_id),
        "sequence": int(sequence),
        "operation": operation.strip().upper(),
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "assignment_revision": assignment_revision_value,
        "captured_at": _canonical_timestamp(captured_at),
        "payload": payload or {},
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical_timestamp(value: datetime) -> str:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode_offline_token(claims: dict[str, Any], *, signing_key: str) -> str:
    """HMAC-SHA256 signed compact token. Claims are public; the signature is authority."""
    body = json.dumps(
        {"typ": OFFLINE_TOKEN_TYPE, "v": OFFLINE_TOKEN_VERSION, **claims},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    encoded_body = _b64url_encode(body)
    signature = hmac.new(
        signing_key.encode("utf-8"),
        encoded_body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_body}.{_b64url_encode(signature)}"


def decode_offline_token(token: str, *, signing_key: str) -> dict[str, Any]:
    """Verify the signature before reading any claim. Raises InvalidOfflineToken."""
    encoded_body, separator, encoded_signature = (token or "").partition(".")
    if not separator or not encoded_body or not encoded_signature:
        raise InvalidOfflineToken("malformed offline authorization token")
    expected = hmac.new(
        signing_key.encode("utf-8"),
        encoded_body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        presented = _b64url_decode(encoded_signature)
    except (ValueError, TypeError) as exc:
        raise InvalidOfflineToken("malformed offline authorization signature") from exc
    if not hmac.compare_digest(expected, presented):
        raise InvalidOfflineToken("offline authorization signature mismatch")
    try:
        claims = json.loads(_b64url_decode(encoded_body))
    except (ValueError, TypeError) as exc:
        raise InvalidOfflineToken("malformed offline authorization claims") from exc
    if not isinstance(claims, dict):
        raise InvalidOfflineToken("malformed offline authorization claims")
    if claims.get("typ") != OFFLINE_TOKEN_TYPE:
        raise InvalidOfflineToken("unexpected offline authorization token type")
    if int(claims.get("v", 0)) != OFFLINE_TOKEN_VERSION:
        raise InvalidOfflineToken("unsupported offline authorization token version")
    return claims
