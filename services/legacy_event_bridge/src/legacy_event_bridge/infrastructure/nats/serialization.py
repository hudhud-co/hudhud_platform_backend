"""Deterministic envelope wire bytes from stored outbox JSON."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from event_envelope.primitives import format_utc_datetime, format_uuid


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return format_uuid(value)
    if isinstance(value, datetime):
        return format_utc_datetime(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    raise TypeError(type(value))


def envelope_dict_to_wire_bytes(payload_json: dict[str, Any]) -> bytes:
    """Serialize stored envelope dict to canonical UTF-8 JSON bytes."""
    encoded = json.dumps(
        payload_json,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return encoded.encode("utf-8")
