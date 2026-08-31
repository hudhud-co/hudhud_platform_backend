"""Docker integration tests proving PostgreSQL polling failure modes (ADR-0007)."""

from __future__ import annotations

import pytest

from .conftest import load_matrix
from .docker_helpers import (
    compose_down,
    compose_up,
    dedicated_resources_absent,
    docker_available,
    psql,
)
from .runner import PHASED_RUNNERS, run_phased_scenario, run_single_poll_scenario
from .scenarios import SCENARIOS
from .strategies import STRATEGY_NAMES

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def polling_lab_stack():
    if not docker_available():
        pytest.skip("Docker runtime not available")
    compose_up()
    yield
    compose_down()
    assert dedicated_resources_absent(), "polling lab resources still present after teardown"


def _should_skip(strategy: str, scenario_id: str) -> bool:
    return (
        (strategy == "updated_at_only" and scenario_id != "update_after_hwm")
        or (
            strategy == "monotonic_sequence"
            and scenario_id
            not in {"monotonic_sequence_append", "sequence_allocation_not_commit_order"}
        )
        or (scenario_id == "update_after_hwm" and strategy != "updated_at_only")
        or (
            scenario_id == "sequence_allocation_not_commit_order"
            and strategy != "monotonic_sequence"
        )
    )


def _run_scenario(scenario_id: str, strategy: str):
    if scenario_id in PHASED_RUNNERS or (
        scenario_id in {"same_timestamp_tiebreak", "timestamp_uuid_composite"}
        and strategy == "uuid_only"
    ):
        return run_phased_scenario(scenario_id, strategy)
    return run_single_poll_scenario(SCENARIOS[scenario_id], strategy)


@pytest.mark.parametrize("scenario_id", SCENARIOS.keys())
@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_scenario_matches_matrix(
    polling_lab_stack,
    scenario_id: str,
    strategy: str,
) -> None:
    if _should_skip(strategy, scenario_id):
        pytest.skip("strategy not applicable to this scenario")

    matrix = load_matrix()
    expected = matrix["expected_outcomes"][scenario_id][strategy]
    result = _run_scenario(scenario_id, strategy)

    assert result.outcome == expected, (
        f"{scenario_id}/{strategy}: expected {expected}, got {result.outcome}, "
        f"captured={result.captured_ids}"
    )


def test_postgres_composite_cursor_ordering(polling_lab_stack) -> None:
    ordered = psql(
        """
SELECT id::text FROM (
  VALUES
    ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2'::uuid, '2026-01-01T12:00:00Z'::timestamptz),
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'::uuid, '2026-01-01T12:00:00Z'::timestamptz)
) AS v(id, occurred_at)
ORDER BY occurred_at, id;
"""
    )
    lines = [line for line in ordered.splitlines() if line.strip()]
    assert lines[0].startswith("aaaaaaaa")


def test_cleanup_removes_dedicated_resources() -> None:
    if not docker_available():
        pytest.skip("Docker runtime not available")
    compose_up()
    compose_down()
    assert dedicated_resources_absent()
