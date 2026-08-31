"""Static PostgreSQL adapter conformance checks."""

from __future__ import annotations

from pathlib import Path


def test_postgres_outbox_claim_uses_skip_locked() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "legacy_event_bridge"
        / "infrastructure"
        / "persistence"
        / "postgres_store.py"
    ).read_text(encoding="utf-8")
    assert "with_for_update(skip_locked=True)" in source
    assert "FOR UPDATE SKIP LOCKED" not in source  # SQLAlchemy API, not raw string


def test_postgres_landing_uses_unique_constraint_conflict() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "legacy_event_bridge"
        / "infrastructure"
        / "persistence"
        / "postgres_store.py"
    ).read_text(encoding="utf-8")
    assert "on_conflict_do_nothing" in source
    assert "source_system" in source
    assert "source_table" in source
    assert "source_pk" in source
