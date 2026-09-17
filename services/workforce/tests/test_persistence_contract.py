"""Static guards on the Workforce schema and its migration."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from workforce.domain.entities import (
    AttendanceRecord,
    DriverApplication,
    DriverProfile,
    LatenessBlock,
    LeaveRequest,
    ShiftPattern,
)
from workforce.domain.messaging import InboxRecord
from workforce.infrastructure.persistence.models import (
    AttendanceRow,
    Base,
    DriverApplicationRow,
    DriverProfileRow,
    IntegrationInboxRow,
    LatenessBlockRow,
    LeaveRequestRow,
    ShiftPatternRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Composite value objects are flattened into columns and asserted separately below.
_PAIRS = (
    (DriverApplication, DriverApplicationRow, {"vehicle"}),
    (DriverProfile, DriverProfileRow, {"vehicle"}),
    (ShiftPattern, ShiftPatternRow, set()),
    (AttendanceRecord, AttendanceRow, set()),
    (LatenessBlock, LatenessBlockRow, set()),
    (LeaveRequest, LeaveRequestRow, {"window"}),
    (InboxRecord, IntegrationInboxRow, set()),
)


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_the_vehicle_is_flattened_into_three_columns() -> None:
    for row_cls in (DriverApplicationRow, DriverProfileRow):
        columns = set(row_cls.__table__.columns.keys())
        assert {
            "vehicle_kind",
            "vehicle_plate_number",
            "vehicle_model",
        } <= columns, row_cls.__name__


def test_a_leave_window_is_flattened_into_its_kind_dates_and_hours() -> None:
    columns = set(LeaveRequestRow.__table__.columns.keys())
    assert {"kind", "start_date", "end_date", "starts_at", "ends_at"} <= columns


# --------------------------------------------------- SEC-03 in the schema


def test_a_verified_application_always_names_its_operator_and_office() -> None:
    """SEC-03 — a verification nobody performed is not one."""
    source = _migration_source()
    assert "ck_driver_application_verification_is_attributed" in source
    assert "verified_by_actor_id IS NOT NULL" in source
    assert "verified_at_office IS NOT NULL" in source


def test_a_driver_exists_only_once_per_application_and_principal() -> None:
    source = _migration_source()
    assert "uq_driver_application" in source
    assert "uq_driver_principal" in source


def test_one_open_application_per_applicant() -> None:
    source = _migration_source()
    assert "uq_driver_application_one_open" in source
    assert "status IN ('SUBMITTED', 'AWAITING_OFFICE_VERIFICATION')" in source


# --------------------------------------------------- DRV-A in the schema


def test_a_shift_pattern_always_names_at_least_one_window() -> None:
    source = _migration_source()
    assert "ck_shift_pattern_has_a_window" in source
    assert "jsonb_array_length(windows) >= 1" in source


def test_a_pattern_cannot_end_before_it_begins() -> None:
    """Otherwise the shift a driver is part-way through becomes unknowable."""
    assert "ck_shift_pattern_window_ordered" in _migration_source()


def test_attendance_is_recorded_once_per_driver_per_day() -> None:
    assert "uq_attendance_driver_day" in _migration_source()


def test_a_started_shift_always_has_a_start_time() -> None:
    source = _migration_source()
    assert "ck_attendance_started_has_time" in source
    assert "status <> 'STARTED' OR started_at IS NOT NULL" in source


def test_lateness_is_never_negative() -> None:
    assert "ck_attendance_lateness_not_negative" in _migration_source()


def test_only_one_lateness_block_is_open_at_a_time() -> None:
    """Two would each need clearing, and a driver would stay paused after support acted."""
    source = _migration_source()
    assert "uq_lateness_block_one_active" in source
    assert "cleared_at IS NULL" in source


def test_a_cleared_block_always_names_who_cleared_it() -> None:
    """OPS-09."""
    source = _migration_source()
    assert "ck_lateness_block_clearing_is_attributed" in source
    assert "(cleared_at IS NULL) = (cleared_by_actor_id IS NULL)" in source


def test_a_declined_leave_request_always_says_why() -> None:
    source = _migration_source()
    assert "ck_leave_decline_has_reason" in source
    assert "status <> 'DECLINED' OR decision_note IS NOT NULL" in source


def test_hourly_leave_states_its_hours_and_sits_inside_one_day() -> None:
    source = _migration_source()
    assert "ck_leave_hours_match_kind" in source
    assert "ck_leave_hourly_is_one_day" in source


def test_a_leave_window_cannot_end_before_it_starts() -> None:
    assert "ck_leave_window_ordered" in _migration_source()


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert "uq_workforce_inbox_consumer_event" in _migration_source()


def test_there_is_no_outbox_table() -> None:
    """Workforce answers questions over HTTP and publishes none."""
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', _migration_source()))
    assert created
    assert not [name for name in created if "outbox" in name]


# --------------------------------------------------- migration hygiene


def test_every_table_is_created_by_a_migration() -> None:
    source = _migration_source()
    for table in Base.metadata.sorted_tables:
        assert f'"{table.name}"' in source, table.name


def test_every_column_is_created_by_a_migration() -> None:
    source = _migration_source()
    missing = [
        f"{table.name}.{column}"
        for table in Base.metadata.sorted_tables
        for column in table.columns.keys()  # noqa: SIM118
        if f'"{column}"' not in source
    ]
    assert missing == []


def test_the_migration_is_expand_only() -> None:
    source = _migration_source()
    upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
    for forbidden in ("op.drop_", "op.alter_column", "op.execute("):
        assert forbidden not in upgrade, forbidden


def test_the_migration_chain_has_exactly_one_root_and_one_head() -> None:
    revisions: dict[str, str | None] = {}
    for path in MIGRATIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = _value(text, "revision")
        assert revision is not None, path.name
        revisions[revision] = _value(text, "down_revision")
    roots = [r for r, d in revisions.items() if d is None]
    heads = [r for r in revisions if r not in set(revisions.values())]
    assert len(roots) == 1, roots
    assert len(heads) == 1, heads


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    for path in MIGRATIONS.glob("*.py"):
        revision = _value(path.read_text(encoding="utf-8"), "revision")
        assert revision is not None and len(revision) <= 32, path.name


def _migration_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))


def _value(text: str, name: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(f"{name}: str"):
            raw = line.split("=", 1)[1].strip()
            return None if raw == "None" else raw.strip('"').strip("'")
    return None
