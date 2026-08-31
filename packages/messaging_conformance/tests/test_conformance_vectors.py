"""Tests for conformance vectors and in-memory runners."""

from __future__ import annotations

import pytest

from messaging_conformance import (
    CONFORMANCE_VECTORS,
    ConformanceVectorId,
    get_vector,
    run_pure_decision_vector,
    vectors_requiring_postgresql,
    vectors_with_pure_decision_coverage,
)


def test_all_ten_vectors_declared() -> None:
    assert len(CONFORMANCE_VECTORS) == 10
    assert {vector.vector_id for vector in CONFORMANCE_VECTORS} == set(ConformanceVectorId)


def test_postgresql_requirements_documented() -> None:
    postgres_ids = {vector.vector_id for vector in vectors_requiring_postgresql()}
    assert ConformanceVectorId.C9 not in postgres_ids
    assert ConformanceVectorId.C1 in postgres_ids


def test_pure_decision_vectors_run_in_memory() -> None:
    for vector in vectors_with_pure_decision_coverage():
        run_pure_decision_vector(vector.vector_id)


def test_postgres_only_vectors_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="requires a PostgreSQL adapter"):
        run_pure_decision_vector(ConformanceVectorId.C1)


def test_get_vector_lookup() -> None:
    vector = get_vector("C6")
    assert vector.vector_id is ConformanceVectorId.C6
