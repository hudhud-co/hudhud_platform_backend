"""Pure-Python polling cursor strategies (no DB driver; SQL generated for psql)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

Outcome = Literal["complete", "gap", "duplicate_safe", "unprovable"]


@dataclass(frozen=True)
class Cursor:
    ts: datetime | None = None
    row_id: UUID | None = None
    seq: int | None = None
    overlap_seconds: int = 0


@dataclass(frozen=True)
class EventRow:
    row_id: UUID
    occurred_at: datetime
    event_type: str
    capture_seq: int | None = None


STRATEGY_NAMES = (
    "timestamp_only",
    "uuid_only",
    "timestamp_uuid",
    "updated_at_only",
    "overlap_window_dedupe",
    "monotonic_sequence",
)


def _ts_literal(value: datetime | str | None) -> str:
    if value is None:
        return "1970-01-01T00:00:00+00:00"
    if isinstance(value, str):
        return value.replace(" ", "T") if " " in value and "T" not in value else value
    return value.isoformat()


def poll_events_sql(strategy: str, cursor: Cursor, *, table: str = "lab_events") -> str:
    if strategy == "timestamp_only":
        ts = _ts_literal(cursor.ts)
        return (
            f"SELECT id::text, occurred_at, event_type FROM {table} "
            f"WHERE occurred_at > TIMESTAMPTZ '{ts}' "
            f"ORDER BY occurred_at, id;"
        )
    if strategy == "uuid_only":
        row_id = str(cursor.row_id) if cursor.row_id else "00000000-0000-0000-0000-000000000000"
        return (
            f"SELECT id::text, occurred_at, event_type FROM {table} "
            f"WHERE id > '{row_id}'::uuid ORDER BY id;"
        )
    if strategy == "timestamp_uuid":
        ts = _ts_literal(cursor.ts)
        row_id = str(cursor.row_id) if cursor.row_id else "00000000-0000-0000-0000-000000000000"
        return (
            f"SELECT id::text, occurred_at, event_type FROM {table} "
            f"WHERE (occurred_at, id) > (TIMESTAMPTZ '{ts}', '{row_id}'::uuid) "
            f"ORDER BY occurred_at, id;"
        )
    if strategy == "overlap_window_dedupe":
        ts = _ts_literal(cursor.ts)
        lookback = cursor.overlap_seconds
        return (
            f"SELECT id::text, occurred_at, event_type FROM {table} "
            f"WHERE occurred_at >= (TIMESTAMPTZ '{ts}' - INTERVAL '{lookback} seconds') "
            f"ORDER BY occurred_at, id;"
        )
    if strategy == "monotonic_sequence":
        seq = cursor.seq if cursor.seq is not None else 0
        return (
            "SELECT id::text, occurred_at, event_type, capture_seq "
            "FROM lab_events_sequenced "
            f"WHERE capture_seq > {seq} ORDER BY capture_seq;"
        )
    msg = f"unsupported event strategy: {strategy}"
    raise ValueError(msg)


def poll_entities_updated_at_sql(cursor: Cursor) -> str:
    ts = _ts_literal(cursor.ts)
    return (
        "SELECT id::text, name, updated_at FROM lab_entities "
        f"WHERE updated_at > TIMESTAMPTZ '{ts}' ORDER BY updated_at, id;"
    )


def advance_cursor(strategy: str, rows: list[EventRow], cursor: Cursor) -> Cursor:
    if not rows:
        return cursor
    if strategy == "uuid_only":
        last = max(rows, key=lambda row: row.row_id)
        return Cursor(row_id=last.row_id)
    if strategy == "monotonic_sequence":
        last = max(rows, key=lambda row: row.capture_seq or 0)
        return Cursor(seq=last.capture_seq)
    if strategy == "timestamp_only":
        last = max(rows, key=lambda row: row.occurred_at)
        return Cursor(ts=last.occurred_at)
    # timestamp_uuid and overlap_window_dedupe advance on composite key
    last = max(rows, key=lambda row: (row.occurred_at, row.row_id))
    return Cursor(
        ts=last.occurred_at,
        row_id=last.row_id,
        overlap_seconds=cursor.overlap_seconds,
    )


def dedupe_rows(rows: list[EventRow]) -> list[EventRow]:
    seen: set[UUID] = set()
    unique: list[EventRow] = []
    for row in rows:
        if row.row_id in seen:
            continue
        seen.add(row.row_id)
        unique.append(row)
    return unique


def classify_capture(
    expected_ids: set[UUID],
    captured_ids: set[UUID],
    *,
    allow_duplicates: bool = False,
    saw_cross_poll_duplicates: bool = False,
) -> Outcome:
    if not expected_ids:
        return "unprovable"
    missing = expected_ids - captured_ids
    if missing:
        return "gap"
    if saw_cross_poll_duplicates or (
        allow_duplicates and captured_ids != expected_ids and expected_ids.issubset(captured_ids)
    ):
        return "duplicate_safe"
    if captured_ids == expected_ids:
        return "complete"
    return "duplicate_safe" if captured_ids - expected_ids else "complete"


def merge_poll_batches(*batches: list[EventRow]) -> tuple[list[EventRow], bool]:
    seen: set[UUID] = set()
    merged: list[EventRow] = []
    saw_duplicates = False
    for batch in batches:
        for row in batch:
            if row.row_id in seen:
                saw_duplicates = True
            else:
                seen.add(row.row_id)
                merged.append(row)
    return merged, saw_duplicates
