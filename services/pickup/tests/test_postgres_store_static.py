"""Static PostgreSQL adapter conformance checks."""

from __future__ import annotations

from pathlib import Path

from pickup.infrastructure import memory
from pickup.infrastructure.persistence import sqlalchemy_store
from pickup.infrastructure.persistence.models import PickupTaskRow
from pickup.ports import repository as repository_port


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
    assert "IntegrationOutboxRow" in source
    assert "AcceptanceIdempotencyRow" in source


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
    assert "assert_migrations_applied" in source


def test_relay_main_composes_postgres_and_disposes_engine() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "pickup" / "runtime" / "relay_main.py"
    ).read_text(encoding="utf-8")
    assert "SqlAlchemyOutboxRelayStore" in source
    assert "assert_migrations_applied" in source
    assert "engine.dispose" in source
    assert "InMemory" not in source


def test_every_pickup_task_column_is_created_by_some_migration() -> None:
    """A model column with no migration would pass unit tests and fail on deploy."""
    versions = (Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*.py")
    migration_source = "\n".join(path.read_text(encoding="utf-8") for path in versions)

    missing = [
        column
        for column in PickupTaskRow.__table__.columns.keys()  # noqa: SIM118
        if f'"{column}"' not in migration_source
    ]
    assert missing == [], missing


def test_the_stop_outcome_migration_is_expand_only() -> None:
    """Additive columns only: nullable, or defaulted so existing rows read as 'off'."""
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w19c_pickup_stop_outcomes_001.py"
    ).read_text(encoding="utf-8")

    assert "down_revision" in migration
    assert '"w19a_pickup_driver_wave_001"' in migration
    assert "nullable=True" in migration
    # The only NOT NULL column carries a server default, so no backfill is needed.
    assert "server_default=sa.false()" in migration
    assert "def downgrade()" in migration
    assert "drop_column" in migration
    # Expand phase must never drop or retype an existing column.
    assert "alter_column" not in migration


def test_postgres_repositories_implement_every_port_method() -> None:
    """A port method the PostgreSQL adapter never implemented.

    The in-memory adapter backs the unit tests, so a repository method that exists
    only there passes every test and then raises AttributeError on the first real
    request. `list_tasks_for_driver` did exactly that: the port declared it, memory
    implemented it, PostgreSQL did not, and every `/pickup/work-sessions/*` call
    answered 500 against a real database.
    """
    port_names = [
        name
        for name in dir(repository_port)
        if name.endswith("Repository") and isinstance(getattr(repository_port, name), type)
    ]

    missing: list[str] = []
    for port_name in port_names:
        port = getattr(repository_port, port_name)
        required = {
            name
            for name in dir(port)
            if not name.startswith("_") and callable(getattr(port, name, None))
        }
        if not required:
            continue
        for module, label in (
            (sqlalchemy_store, "postgres"),
            (memory, "memory"),
        ):
            implementation = _adapter_for(module, required)
            if implementation is None:
                continue
            for method in sorted(required):
                if not hasattr(implementation, method):
                    missing.append(f"{label}:{implementation.__name__}.{method} ({port_name})")

    assert not missing, "repository adapters missing port methods: " + ", ".join(missing)


def _adapter_for(module, required: set[str]):
    """The adapter class in `module` that implements the most of `required`."""
    best = None
    best_score = 0
    for name in dir(module):
        candidate = getattr(module, name)
        if not isinstance(candidate, type) or not name.startswith("_"):
            continue
        score = sum(1 for method in required if hasattr(candidate, method))
        if score > best_score:
            best, best_score = candidate, score
    # Only treat it as the adapter for this port when it covers most of the surface.
    if best is None or best_score < max(1, len(required) - 1):
        return None
    return best
