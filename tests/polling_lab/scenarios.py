"""Scenario SQL builders and expected row sets for the polling lab."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

# Fixed UUIDs for deterministic ordering proofs.
UUID_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
UUID_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
UUID_C = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc3")
UUID_D = UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd4")
UUID_E = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee5")
UUID_F = UUID("ffffffff-ffff-4fff-8fff-fffffffffff6")


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    setup_sql: str
    expected_event_ids: frozenset[UUID] = frozenset()
    expected_entity_ids: frozenset[UUID] = frozenset()
    allow_overlap_duplicates: bool = False
    unprovable: bool = False


def reset_sql() -> str:
    return """
TRUNCATE lab_events, lab_entities, lab_events_sequenced, lab_bridge_cursor,
         lab_scenario_runs RESTART IDENTITY CASCADE;
"""


SCENARIOS: dict[str, ScenarioSpec] = {
    "uuid_non_monotonic_order": ScenarioSpec(
        scenario_id="uuid_non_monotonic_order",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_C}', '2026-01-01T10:00:00Z', 'c'),
  ('{UUID_A}', '2026-01-01T10:01:00Z', 'a'),
  ('{UUID_B}', '2026-01-01T10:02:00Z', 'b');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C}),
    ),
    "same_timestamp_tiebreak": ScenarioSpec(
        scenario_id="same_timestamp_tiebreak",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T12:00:00Z', 'a'),
  ('{UUID_B}', '2026-01-01T12:00:00Z', 'b'),
  ('{UUID_C}', '2026-01-01T12:00:00Z', 'c');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C}),
    ),
    "late_commit_earlier_ts": ScenarioSpec(
        scenario_id="late_commit_earlier_ts",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:00Z', 'committed_first');
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_C}', '2026-01-01T11:00:00Z', 'advances_hwm');
-- Row B simulates late commit with earlier application timestamp (inserted last).
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_B}', '2026-01-01T09:00:00Z', 'late_commit');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C}),
        allow_overlap_duplicates=True,
    ),
    "long_running_txn_cross_boundary": ScenarioSpec(
        scenario_id="long_running_txn_cross_boundary",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:00Z', 'visible');
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_C}', '2026-01-01T12:00:00Z', 'after_hwm');
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_B}', '2026-01-01T09:30:00Z', 'long_txn_late');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C}),
        allow_overlap_duplicates=True,
    ),
    "update_after_hwm": ScenarioSpec(
        scenario_id="update_after_hwm",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_entities (id, name, updated_at) VALUES
  ('{UUID_A}', 'before', '2026-01-01T10:00:00Z');
UPDATE lab_entities SET name = 'after', updated_at = '2026-01-01T11:00:00Z'
  WHERE id = '{UUID_A}';
""",
        expected_entity_ids=frozenset({UUID_A}),
        allow_overlap_duplicates=True,
    ),
    "hard_delete_no_tombstone": ScenarioSpec(
        scenario_id="hard_delete_no_tombstone",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:00Z', 'present'),
  ('{UUID_B}', '2026-01-01T10:01:00Z', 'deleted');
DELETE FROM lab_events WHERE id = '{UUID_B}';
""",
        expected_event_ids=frozenset({UUID_A}),
        unprovable=True,
    ),
    "persisted_cursor_restart": ScenarioSpec(
        scenario_id="persisted_cursor_restart",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:00Z', 'first'),
  ('{UUID_B}', '2026-01-01T10:01:00Z', 'second'),
  ('{UUID_C}', '2026-01-01T10:02:00Z', 'third');
INSERT INTO lab_bridge_cursor (stream_name, strategy, cursor_ts, cursor_id)
  VALUES ('lab_events', 'timestamp_uuid', '2026-01-01T10:01:00Z', '{UUID_B}');
""",
        expected_event_ids=frozenset({UUID_C}),
    ),
    "overlap_window_dedupe": ScenarioSpec(
        scenario_id="overlap_window_dedupe",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:00Z', 'seen'),
  ('{UUID_C}', '2026-01-01T11:00:00Z', 'hwm'),
  ('{UUID_B}', '2026-01-01T09:30:00Z', 'late');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C}),
        allow_overlap_duplicates=True,
    ),
    "timestamp_uuid_composite": ScenarioSpec(
        scenario_id="timestamp_uuid_composite",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_B}', '2026-01-01T12:00:00Z', 'b'),
  ('{UUID_A}', '2026-01-01T12:00:00Z', 'a'),
  ('{UUID_C}', '2026-01-01T12:00:01Z', 'c');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C}),
    ),
    "monotonic_sequence_append": ScenarioSpec(
        scenario_id="monotonic_sequence_append",
        setup_sql=reset_sql()
        + """
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', '2026-01-01T10:00:00Z', 's1'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2', '2026-01-01T10:01:00Z', 's2'),
  ('cccccccc-cccc-4ccc-8ccc-ccccccccccc3', '2026-01-01T09:00:00Z', 's3');
INSERT INTO lab_events_sequenced (occurred_at, event_type) VALUES
  ('2026-01-01T10:00:00Z', 's1'),
  ('2026-01-01T10:01:00Z', 's2'),
  ('2026-01-01T09:00:00Z', 's3');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C}),
    ),
    "snapshot_post_hwm": ScenarioSpec(
        scenario_id="snapshot_post_hwm",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:00Z', 'pre_hwm');
INSERT INTO lab_bridge_cursor (stream_name, strategy, cursor_ts, cursor_id)
  SELECT 'lab_events', 'snapshot_hwm', occurred_at, id FROM lab_events
  WHERE id = '{UUID_A}';
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_B}', '2026-01-01T10:01:00Z', 'post_hwm'),
  ('{UUID_C}', '2026-01-01T10:02:00Z', 'post_hwm2');
""",
        expected_event_ids=frozenset({UUID_B, UUID_C}),
    ),
    "concurrent_writers": ScenarioSpec(
        scenario_id="concurrent_writers",
        setup_sql=reset_sql()
        + f"""
INSERT INTO lab_events (id, occurred_at, event_type) VALUES
  ('{UUID_A}', '2026-01-01T10:00:00Z', 'w1'),
  ('{UUID_B}', '2026-01-01T10:00:00Z', 'w2'),
  ('{UUID_C}', '2026-01-01T10:00:00Z', 'w3'),
  ('{UUID_D}', '2026-01-01T10:00:01Z', 'w4'),
  ('{UUID_E}', '2026-01-01T10:00:01Z', 'w5'),
  ('{UUID_F}', '2026-01-01T10:00:01Z', 'w6');
""",
        expected_event_ids=frozenset({UUID_A, UUID_B, UUID_C, UUID_D, UUID_E, UUID_F}),
    ),
}

SCENARIO_IDS = tuple(SCENARIOS.keys())
