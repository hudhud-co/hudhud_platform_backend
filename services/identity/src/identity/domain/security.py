"""Keyed hashing and secret generation for the Identity service.

Nothing here ever stores or returns a secret in a recoverable form. An OTP code and a
session token exist in plaintext exactly once — in the response that delivers them — and
only their keyed hash is persisted. Every comparison is constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

#: Codes are numeric so they can be read aloud over the phone and typed on a numeric pad.
OTP_CODE_LENGTH = 6

#: Iraqi numbers are the only ones the product serves today, but validation is deliberately
#: shape-based rather than country-specific so a second country needs no code change.
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_NON_DIGIT = re.compile(r"[^\d+]")


def normalize_phone(raw: str) -> str:
    """Reduce a phone number to E.164 so one person has exactly one identity.

    The Customer and Driver apps both let people type spaces and local prefixes
    (``770 182 0934``). Without normalisation the same person would be able to create two
    principals by typing their number differently.
    """
    candidate = _NON_DIGIT.sub("", (raw or "").strip())
    if candidate.startswith("00"):
        candidate = "+" + candidate[2:]
    if not candidate.startswith("+"):
        # A bare national number is meaningless without a country; refuse rather than guess.
        raise ValueError("phone number must include a country code")
    if not _E164.match(candidate):
        raise ValueError("phone number is not a valid E.164 number")
    return candidate


def phone_last4(normalized: str) -> str:
    """The only part of a phone number safe to show back or log."""
    return normalized[-4:]


def hash_phone(normalized: str, *, signing_key: str) -> str:
    """Keyed lookup handle for a phone number.

    Keyed rather than plain: a stolen table of plain SHA-256 phone hashes is trivially
    reversed, because the search space of phone numbers is tiny.
    """
    return _hmac(f"phone:{normalized}", signing_key)


def generate_otp_code() -> str:
    """A uniformly random numeric code. Never derived from time or from the phone."""
    return "".join(secrets.choice("0123456789") for _ in range(OTP_CODE_LENGTH))


def hash_otp_code(code: str, *, challenge_id: str, signing_key: str) -> str:
    """Bind the code hash to its challenge so a hash cannot be replayed on another."""
    return _hmac(f"otp:{challenge_id}:{code}", signing_key)


def verify_otp_code(code: str, *, challenge_id: str, signing_key: str, expected: str) -> bool:
    return hmac.compare_digest(
        hash_otp_code(code, challenge_id=challenge_id, signing_key=signing_key), expected
    )


def generate_session_token() -> str:
    """Opaque bearer token. Not a JWT: revocation must be immediate and server-side."""
    return secrets.token_urlsafe(48)


def hash_session_token(token: str, *, signing_key: str) -> str:
    return _hmac(f"session:{token}", signing_key)


def verify_session_token(token: str, *, signing_key: str, expected: str) -> bool:
    return hmac.compare_digest(hash_session_token(token, signing_key=signing_key), expected)


def hash_device_id(device_id: str, *, signing_key: str) -> str:
    """Device identifiers are client-supplied, so they are stored only as a keyed hash."""
    return _hmac(f"device:{device_id}", signing_key)


def _hmac(material: str, signing_key: str) -> str:
    return hmac.new(
        signing_key.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
    ).hexdigest()
