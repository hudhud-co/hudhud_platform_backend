"""Keyed hashing for the identifiers this service must not keep in the clear.

A notification record has to know *who* it went to so it can be deduplicated, retried and
audited — but it does not need to keep a readable phone number to do that. Hashing the
number with a service key gives a stable lookup key that is useless if the table leaks,
and the last four digits are kept separately for display, exactly as the apps show them.

The delivery code itself is never stored at all. It is passed to the transport and
dropped; see `dispatch_service`.
"""

from __future__ import annotations

import hashlib
import hmac


def hash_recipient(value: str, *, key: str) -> str:
    """Stable, keyed digest of a phone number or address.

    Keyed rather than plain: a plain SHA-256 of a phone number is trivially reversed by
    enumerating the Iraqi numbering plan.
    """
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
