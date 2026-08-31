"""Lease expiry decision helpers."""

from __future__ import annotations

from datetime import datetime


def is_lease_expired(lease_until: datetime | None, *, now: datetime) -> bool:
    """Return True when a processing lease has elapsed."""
    if lease_until is None:
        return True
    return now >= lease_until


def is_lease_active(lease_until: datetime | None, *, now: datetime) -> bool:
    """Return True when a processing lease is still held."""
    return lease_until is not None and now < lease_until
