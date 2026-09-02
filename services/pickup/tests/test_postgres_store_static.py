"""Static PostgreSQL adapter conformance checks."""

from __future__ import annotations

from pathlib import Path


def test_sqlalchemy_store_declares_recovery_tables() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "pickup"
        / "infrastructure"
        / "persistence"
        / "sqlalchemy_store.py"
    ).read_text(encoding="utf-8")
    assert "PickupTaskRow" in source
    assert "RecoveryHistoryRow" in source
    assert "RecoveryIdempotencyRow" in source
    assert "SqlAlchemyRecoveryUnitOfWork" in source
    assert "StalePickupTaskVersion" in source


def test_migration_declares_lineage_and_idempotency_uniqueness() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w15b_pickup_recovery_001_initial.py"
    ).read_text(encoding="utf-8")
    assert "uq_pickup_tasks_root_attempt_number" in migration
    assert "uq_pickup_recovery_history_idempotency_key" in migration
    assert "pickup_recovery_idempotency" in migration


def test_session_factory_exports_async_and_sync_builders() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "pickup"
        / "infrastructure"
        / "persistence"
        / "session.py"
    ).read_text(encoding="utf-8")
    assert "build_async_session_factory" in source
    assert "build_session_factory" in source
    assert "build_engine" in source
