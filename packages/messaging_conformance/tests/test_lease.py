"""Tests for lease helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from messaging_conformance import is_lease_active, is_lease_expired


def test_none_lease_is_expired() -> None:
    now = datetime.now(tz=UTC)
    assert is_lease_expired(None, now=now)
    assert not is_lease_active(None, now=now)


def test_future_lease_is_active() -> None:
    now = datetime.now(tz=UTC)
    lease_until = now + timedelta(seconds=10)
    assert is_lease_active(lease_until, now=now)
    assert not is_lease_expired(lease_until, now=now)


def test_past_lease_is_expired() -> None:
    now = datetime.now(tz=UTC)
    lease_until = now - timedelta(seconds=1)
    assert is_lease_expired(lease_until, now=now)
    assert not is_lease_active(lease_until, now=now)
