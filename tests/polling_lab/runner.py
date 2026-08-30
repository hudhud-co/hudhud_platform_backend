"""Multi-phase scenario execution for polling integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .docker_helpers import fetch_events, load_persisted_cursor, psql
from .scenarios import (
    UUID_A,
    UUID_B,
    UUID_C,
    UUID_D,
    UUID_E,
    UUID_F,
    ScenarioSpec,
    reset_sql,
)
from .strategies import Cursor, advance_cursor, classify_capture, dedupe_rows, merge_poll_batches


@dataclass
class PhaseResult:
    strategy: str
    outcome: str
    captured_ids: set[UUID]


def run_single_poll_scenario(spec: ScenarioSpec, strategy: str) -> PhaseResult:
    psql(spec.setup_sql)
    cursor = Cursor()

    if spec.scenario_id == "persisted_cursor_restart":
        cursor = load_persisted_cursor("timestamp_uuid")
    elif spec.scenario_id == "snapshot_post_hwm":
        cursor = load_persisted_cursor("snapshot_hwm")

    if strategy == "overlap_window_dedupe":
        cursor = Cursor(
            ts=cursor.ts,
            row_id=cursor.row_id,
            overlap_seconds=7200,
        )

    rows = fetch_events(strategy, cursor)
    if strategy == "overlap_window_dedupe":
        rows = dedupe_rows(rows)

    if spec.scenario_id == "monotonic_sequence_append" and strategy == "monotonic_sequence":
        expected_count = int(psql("SELECT COUNT(*) FROM lab_events_sequenced;").strip())
        if len(rows) == expected_count:
            outcome = "complete"
        elif len(rows) < expected_count:
            outcome = "gap"
        else:
            outcome = "duplicate_safe"
        return PhaseResult(
            strategy=strategy,
            outcome=outcome,
            captured_ids={row.row_id for row in rows},
        )

    if spec.unprovable:
        return PhaseResult(
            strategy=strategy,
            outcome="unprovable",
            captured_ids={row.row_id for row in rows},
        )

    captured_ids = {row.row_id for row in rows}
    outcome = classify_capture(
        set(spec.expected_event_ids),
        captured_ids,
        allow_duplicates=strategy == "overlap_window_dedupe",
    )
    return PhaseResult(strategy=strategy, outcome=outcome, captured_ids=captured_ids)


def _overlap_cursor(cursor: Cursor, strategy: str) -> Cursor:
    if strategy == "overlap_window_dedupe":
        return Cursor(
            ts=cursor.ts,
            row_id=cursor.row_id,
            overlap_seconds=7200,
        )
    return cursor


def run_uuid_non_monotonic_phased(strategy: str) -> PhaseResult:
    psql(reset_sql())
    psql(
        f"INSERT INTO lab_events (id, occurred_at, event_type) VALUES "
        f"('{UUID_C}', '2026-01-01T10:00:00Z', 'first');"
    )
    cursor = Cursor()
    first = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    cursor = advance_cursor(strategy, first, cursor)

    psql(
        f"INSERT INTO lab_events (id, occurred_at, event_type) VALUES "
        f"('{UUID_A}', '2026-01-01T10:01:00Z', 'earlier_uuid');"
    )
    second = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    all_rows = dedupe_rows(first + second)
    captured_ids = {row.row_id for row in all_rows}
    outcome = classify_capture({UUID_A, UUID_C}, captured_ids)
    return PhaseResult(strategy=strategy, outcome=outcome, captured_ids=captured_ids)


def run_late_commit_phased(strategy: str) -> PhaseResult:
    psql(reset_sql())
    psql(
        f"INSERT INTO lab_events (id, occurred_at, event_type) VALUES "
        f"('{UUID_A}', '2026-01-01T10:00:00Z', 'a');"
    )
    cursor = Cursor()
    first = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    cursor = advance_cursor(strategy, first, cursor)

    psql(
        f"INSERT INTO lab_events (id, occurred_at, event_type) VALUES "
        f"('{UUID_C}', '2026-01-01T11:00:00Z', 'hwm');"
    )
    second = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    cursor = advance_cursor(strategy, second, cursor)

    psql(
        f"INSERT INTO lab_events (id, occurred_at, event_type) VALUES "
        f"('{UUID_B}', '2026-01-01T09:00:00Z', 'late');"
    )
    third = fetch_events(strategy, _overlap_cursor(cursor, strategy))

    all_rows, saw_duplicates = merge_poll_batches(first, second, third)
    captured_ids = {row.row_id for row in all_rows}
    allow_dupes = strategy == "overlap_window_dedupe"
    outcome = classify_capture(
        {UUID_A, UUID_B, UUID_C},
        captured_ids,
        allow_duplicates=allow_dupes,
        saw_cross_poll_duplicates=saw_duplicates,
    )
    return PhaseResult(strategy=strategy, outcome=outcome, captured_ids=captured_ids)


def run_concurrent_writers_phased(strategy: str) -> PhaseResult:
    psql(reset_sql())
    psql(
        f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_D}', '2026-01-01T10:00:00Z', 'batch1'),
  ('{UUID_E}', '2026-01-01T10:00:00Z', 'batch1'),
  ('{UUID_F}', '2026-01-01T10:00:01Z', 'batch1');
"""
    )
    cursor = Cursor()
    first = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    cursor = advance_cursor(strategy, first, cursor)
    psql(
        f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:02Z', 'batch2'),
  ('{UUID_B}', '2026-01-01T10:00:02Z', 'batch2'),
  ('{UUID_C}', '2026-01-01T10:00:03Z', 'batch2');
"""
    )
    second = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    all_rows = dedupe_rows(first + second)
    captured_ids = {row.row_id for row in all_rows}
    outcome = classify_capture(
        {UUID_A, UUID_B, UUID_C, UUID_D, UUID_E, UUID_F},
        captured_ids,
    )
    return PhaseResult(strategy=strategy, outcome=outcome, captured_ids=captured_ids)


def run_update_after_hwm_phased(strategy: str) -> PhaseResult:
    psql(reset_sql())
    psql(
        f"INSERT INTO lab_entities (id, name, updated_at) VALUES "
        f"('{UUID_A}', 'before', '2026-01-01T10:00:00Z');"
    )
    cursor = Cursor()
    first = fetch_events("updated_at_only", cursor)
    cursor = advance_cursor("timestamp_only", first, cursor)

    psql(
        f"UPDATE lab_entities SET name = 'after', updated_at = '2026-01-01T11:00:00Z' "
        f"WHERE id = '{UUID_A}';"
    )
    second = fetch_events("updated_at_only", cursor)
    all_rows, saw_duplicates = merge_poll_batches(first, second)
    captured_ids = {row.row_id for row in all_rows}
    outcome = classify_capture(
        {UUID_A}, captured_ids, allow_duplicates=True, saw_cross_poll_duplicates=saw_duplicates
    )
    return PhaseResult(strategy=strategy, outcome=outcome, captured_ids=captured_ids)


def run_same_timestamp_phased(strategy: str) -> PhaseResult:
    psql(reset_sql())
    psql(
        f"INSERT INTO lab_events (id, occurred_at, event_type) VALUES "
        f"('{UUID_A}', '2026-01-01T12:00:00Z', 'a');"
    )
    cursor = Cursor()
    first = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    cursor = advance_cursor(strategy, first, cursor)

    psql(
        f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_B}', '2026-01-01T12:00:00Z', 'b'),
  ('{UUID_C}', '2026-01-01T12:00:00Z', 'c');
"""
    )
    second = fetch_events(strategy, _overlap_cursor(cursor, strategy))
    all_rows, saw_duplicates = merge_poll_batches(first, second)
    captured_ids = {row.row_id for row in all_rows}
    outcome = classify_capture(
        {UUID_A, UUID_B, UUID_C},
        captured_ids,
        allow_duplicates=strategy == "overlap_window_dedupe",
        saw_cross_poll_duplicates=saw_duplicates,
    )
    return PhaseResult(strategy=strategy, outcome=outcome, captured_ids=captured_ids)


def run_uuid_order_phased(strategy: str) -> PhaseResult:
    psql(reset_sql())
    psql(
        f"INSERT INTO lab_events (id, occurred_at, event_type) VALUES "
        f"('{UUID_C}', '2026-01-01T12:00:00Z', 'c');"
    )
    cursor = Cursor()
    first = fetch_events(strategy, cursor)
    cursor = advance_cursor(strategy, first, cursor)

    psql(
        f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T12:00:00Z', 'a'),
  ('{UUID_B}', '2026-01-01T12:00:00Z', 'b');
"""
    )
    second = fetch_events(strategy, cursor)
    all_rows = dedupe_rows(first + second)
    captured_ids = {row.row_id for row in all_rows}
    outcome = classify_capture({UUID_A, UUID_B, UUID_C}, captured_ids)
    return PhaseResult(strategy=strategy, outcome=outcome, captured_ids=captured_ids)


def run_timestamp_uuid_composite_phased(strategy: str) -> PhaseResult:
    if strategy == "uuid_only":
        return run_uuid_order_phased(strategy)
    return run_same_timestamp_phased(strategy)


PHASED_RUNNERS = {
    "uuid_non_monotonic_order": run_uuid_non_monotonic_phased,
    "same_timestamp_tiebreak": run_same_timestamp_phased,
    "timestamp_uuid_composite": run_timestamp_uuid_composite_phased,
    "late_commit_earlier_ts": run_late_commit_phased,
    "long_running_txn_cross_boundary": run_late_commit_phased,
    "overlap_window_dedupe": run_late_commit_phased,
    "concurrent_writers": run_concurrent_writers_phased,
    "update_after_hwm": run_update_after_hwm_phased,
}


def run_phased_scenario(scenario_id: str, strategy: str) -> PhaseResult:
    uuid_phased = {"same_timestamp_tiebreak", "timestamp_uuid_composite"}
    if scenario_id in uuid_phased and strategy == "uuid_only":
        return run_uuid_order_phased(strategy)
    return PHASED_RUNNERS[scenario_id](strategy)
