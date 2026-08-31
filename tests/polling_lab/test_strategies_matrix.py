"""Static assertions on strategy matrix semantics (no Docker)."""

from __future__ import annotations

from .conftest import load_matrix


def test_proven_unsafe_strategies_documented() -> None:
    matrix = load_matrix()
    unsafe = matrix["proven_unsafe_for_legacy_bridge"]
    assert any("uuid_only" in item for item in unsafe)
    assert any("updated_at_only" in item for item in unsafe)
    assert any("tombstone" in item for item in unsafe)


def test_monotonic_sequence_does_not_prove_completeness() -> None:
    matrix = load_matrix()
    seq = matrix["strategies"]["monotonic_sequence"]
    assert seq["requires_schema_change"] is True
    assert seq["can_prove_completeness"] is False


def test_overlap_duplicate_safe_is_not_gap_free() -> None:
    matrix = load_matrix()
    overlap = matrix["strategies"]["overlap_window_dedupe"]
    assert overlap.get("duplicate_safe_not_gap_free") is True
    assert overlap.get("requires_bounded_lateness") is True


def test_sequence_allocation_scenario_expects_gap() -> None:
    matrix = load_matrix()
    outcomes = matrix["expected_outcomes"]["sequence_allocation_not_commit_order"]
    assert outcomes["monotonic_sequence"] == "gap"


def test_snapshot_post_hwm_is_illustrative_not_zero_gap_proof() -> None:
    matrix = load_matrix()
    meta = matrix["scenarios"]["snapshot_post_hwm"]
    assert meta.get("evidence_class") == "synthetic_illustrative_not_production_zero_gap_proof"
    outcomes = matrix["expected_outcomes"]["snapshot_post_hwm"]
    assert outcomes["timestamp_uuid"] == "duplicate_safe"
    assert outcomes["monotonic_sequence"] == "duplicate_safe"


def test_timestamp_uuid_cannot_prove_completeness() -> None:
    matrix = load_matrix()
    strategy = matrix["strategies"]["timestamp_uuid"]
    assert strategy["can_prove_completeness"] is False


def test_late_commit_gap_for_timestamp_uuid() -> None:
    matrix = load_matrix()
    outcome = matrix["expected_outcomes"]["late_commit_earlier_ts"]["timestamp_uuid"]
    assert outcome == "gap"


def test_overlap_window_duplicate_safe_on_late_commit() -> None:
    matrix = load_matrix()
    outcome = matrix["expected_outcomes"]["late_commit_earlier_ts"]["overlap_window_dedupe"]
    assert outcome == "duplicate_safe"


def test_hard_delete_unprovable_for_all_strategies() -> None:
    matrix = load_matrix()
    outcomes = matrix["expected_outcomes"]["hard_delete_no_tombstone"]
    assert all(value == "unprovable" for value in outcomes.values())


def test_evidence_classification_present() -> None:
    matrix = load_matrix()
    assert "postgresql_behavior" in matrix["evidence_classification"]
    assert "legacy_suitability_inference" in matrix["evidence_classification"]
    assert "unresolved_production_evidence" in matrix["evidence_classification"]
