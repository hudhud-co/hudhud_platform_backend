"""Build canonical A2 envelopes for the live eventing proof."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPLETE_ENVELOPE = (
    REPO_ROOT
    / "contracts"
    / "events"
    / "legacy_bridge.observation.audit_entry"
    / "examples"
    / "complete_envelope.json"
)

A2_EVENT_ID_NAMESPACE = UUID("697097cc-6afb-556b-9f9b-4be135ca6282")
A2_SOURCE_SYSTEM = "legacy"
A2_SOURCE_TABLE = "audit_logs"


def _append_only_event_id(*, source_pk: str) -> UUID:
    name = f"{A2_SOURCE_SYSTEM}:{A2_SOURCE_TABLE}:{source_pk}"
    return uuid5(A2_EVENT_ID_NAMESPACE, name)


def build_a2_envelope(*, source_pk: UUID | None = None) -> dict[str, Any]:
    """Return a deep copy of the canonical complete envelope with a unique source row."""
    envelope = copy.deepcopy(json.loads(COMPLETE_ENVELOPE.read_text(encoding="utf-8")))
    pk = source_pk or uuid4()
    pk_text = str(pk)
    event_id = _append_only_event_id(source_pk=pk_text)
    envelope["event_id"] = str(event_id)
    envelope["correlation_id"] = str(uuid4())
    payload = envelope["payload"]
    payload["source_pk"] = pk_text
    payload["audit_entry_id"] = pk_text
    payload["source_position"] = f"0/{pk.hex[:8].upper()}"
    return envelope
