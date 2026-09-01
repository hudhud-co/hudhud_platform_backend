"""Opaque versioned URL-safe cursors for timeline pagination."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from tracking.domain.types import TimelinePageCursor

CURSOR_VERSION = 1


class TimelineCursorError(ValueError):
    """Malformed or mismatched timeline cursor."""


def encode_timeline_cursor(shipment_id: UUID, cursor: TimelinePageCursor) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "s": str(shipment_id),
        "o": cursor.occurred_at.isoformat(),
        "e": str(cursor.event_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_timeline_cursor(shipment_id: UUID, encoded: str) -> TimelinePageCursor:
    if not encoded or not encoded.strip():
        msg = "cursor is empty"
        raise TimelineCursorError(msg)
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        msg = "cursor is not valid base64/json"
        raise TimelineCursorError(msg) from exc
    if not isinstance(payload, dict):
        msg = "cursor payload must be an object"
        raise TimelineCursorError(msg)
    version = payload.get("v")
    if version != CURSOR_VERSION:
        msg = "unsupported cursor version"
        raise TimelineCursorError(msg)
    cursor_shipment = payload.get("s")
    if cursor_shipment != str(shipment_id):
        msg = "cursor shipment scope mismatch"
        raise TimelineCursorError(msg)
    occurred_raw = payload.get("o")
    event_raw = payload.get("e")
    if not isinstance(occurred_raw, str) or not isinstance(event_raw, str):
        msg = "cursor missing occurred_at or event_id"
        raise TimelineCursorError(msg)
    try:
        occurred_at = datetime.fromisoformat(occurred_raw)
        event_id = UUID(event_raw)
    except (ValueError, TypeError) as exc:
        msg = "cursor contains invalid occurred_at or event_id"
        raise TimelineCursorError(msg) from exc
    return TimelinePageCursor(occurred_at=occurred_at, event_id=event_id)
