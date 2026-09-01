"""Sanitized error helper tests."""

from __future__ import annotations

from messaging_conformance.conformance.assertions import assert_sanitized_error_message

from tracking.domain.sanitize import sanitize_error_message


def test_sanitize_redacts_jwt_and_connection_strings() -> None:
    raw = (
        "handler failed jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb "
        "dsn=postgresql://user:secret@localhost/audit"
    )
    cleaned = sanitize_error_message(raw)
    assert_sanitized_error_message(cleaned, context="sanitize")
    assert "eyJ" not in cleaned
    assert "postgresql://" not in cleaned
    assert "[redacted]" in cleaned
