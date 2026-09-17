"""Static guards on the Claims schema and its migration.

The mapping between an entity and its row is where a field quietly stops being
persisted. These tests walk both sides, so adding a field to an entity without a column
fails here rather than in production.

Two of them guard an **absence**: SEC-07's "no compensation or claim value is shown to
the driver" only holds if there is nowhere in the incident table to put one.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from claims.domain.entities import (
    ClaimMessage,
    ClaimSummaryForDriver,
    CompensationClaim,
    DriverIncident,
)
from claims.domain.messaging import InboxRecord, OutboxRecord
from claims.infrastructure.persistence.models import (
    Base,
    ClaimMessageRow,
    CompensationClaimRow,
    DriverIncidentRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Composite value objects are flattened into columns and asserted separately below.
_PAIRS = (
    (CompensationClaim, CompensationClaimRow, {"evidence", "compensation_amount"}),
    (ClaimMessage, ClaimMessageRow, {"attachments"}),
    (DriverIncident, DriverIncidentRow, {"evidence"}),
    (OutboxRecord, IntegrationOutboxRow, set()),
    (InboxRecord, IntegrationInboxRow, set()),
)


def _migration_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_money_is_flattened_into_minor_units_and_a_currency() -> None:
    """Exact IQD: never one column holding a formatted amount."""
    columns = set(CompensationClaimRow.__table__.columns.keys())
    assert "compensation_minor_units" in columns
    assert "compensation_currency" in columns


def test_no_monetary_column_is_a_float_or_a_numeric() -> None:
    offenders = [
        f"{table.name}.{column.name}: {column.type}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name.endswith("_minor_units")
        and "INT" not in str(column.type).upper()
    ]
    assert offenders == []


def test_no_column_anywhere_is_a_float() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if "FLOAT" in str(column.type).upper()
        or "DOUBLE" in str(column.type).upper()
        or "NUMERIC" in str(column.type).upper()
    ]
    assert offenders == []


def test_evidence_is_a_pointer_and_never_the_bytes() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if "BLOB" in str(column.type).upper() or "BYTEA" in str(column.type).upper()
    ]
    assert offenders == []


def test_every_mutable_aggregate_carries_a_version() -> None:
    """Optimistic concurrency needs somewhere to keep the version."""
    for row_cls in (CompensationClaimRow, DriverIncidentRow):
        assert "version" in row_cls.__table__.columns, row_cls.__name__


# ----------------------------------------------------- SEC-07 by absence


def test_the_incident_table_has_no_compensation_column() -> None:
    """SEC-07 — "No compensation or claim value is shown to the driver".

    A driver-facing promise that depends on remembering to filter is a promise waiting
    to be broken by the next query. There is nowhere in this table to put an amount.
    """
    offenders = [
        column.name
        for column in DriverIncidentRow.__table__.columns
        if "compensation" in column.name
        or "amount" in column.name
        or "minor_units" in column.name
    ]
    assert offenders == []


def test_the_driver_summary_has_no_field_an_amount_could_go_in() -> None:
    fields = {f.name for f in dataclasses.fields(ClaimSummaryForDriver)}
    assert not any(
        "amount" in name or "compensation" in name or "value" in name for name in fields
    )


def test_the_incident_migration_carries_no_compensation_column() -> None:
    """In the DDL as well as in the models."""
    source = _migration_source()
    incident_ddl = source.split('"claims_driver_incidents"', 1)[1].split(
        "op.create_index", 1
    )[0]
    assert "compensation" not in incident_ddl
    assert "minor_units" not in incident_ddl


# ----------------------------------------------------- the migration matches


def test_every_model_column_appears_in_the_migration() -> None:
    source = _migration_source()
    missing = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if f'"{column.name}"' not in source
    ]
    assert missing == []


def test_every_model_table_appears_in_the_migration() -> None:
    source = _migration_source()
    missing = [
        table.name
        for table in Base.metadata.sorted_tables
        if f'"{table.name}"' not in source
    ]
    assert missing == []


def test_the_migration_is_expand_only() -> None:
    """W28 creates new tables and touches nothing that exists."""
    source = _migration_source()
    assert "op.drop_column" not in source
    assert "op.alter_column" not in source


def test_the_migration_declares_a_head_and_no_parent() -> None:
    source = _migration_source()
    assert 'revision: str = "w28_claims_core_001"' in source
    assert "down_revision: str | Sequence[str] | None = None" in source


def test_the_revision_identifier_fits_alembics_column() -> None:
    for match in re.findall(r'revision: str = "([^"]+)"', _migration_source()):
        assert len(match) <= 32, match


def test_the_invariants_reach_the_database() -> None:
    """A rule enforced only in Python is a rule one direct UPDATE away from broken."""
    source = _migration_source()
    for constraint in (
        # CLM-03 — the custody review happened before the decision.
        "ck_claims_claim_decided_after_custody_review",
        # CLM-04 — approved carries an amount and a decider; rejected carries a reason.
        "ck_claims_claim_approved_carries_an_amount",
        "ck_claims_claim_rejected_carries_a_reason",
        "ck_claims_claim_amount_only_when_approved",
        "ck_claims_claim_amount_has_currency",
        # v6.3 p.37 — filed only where HUDHUD still carried the risk.
        "ck_claims_claim_filed_within_hudhud_liability",
        # CLM-02 — one open claim per parcel, whoever files.
        "uq_claims_one_open_per_parcel",
        # Driver App v8 — a breakdown has no parcel, everything else has one.
        "ck_claims_incident_parcel_matches_kind",
        # OPS-07 — resolved means operations said what was found.
        "ck_claims_incident_resolved_is_documented",
        "uq_claims_outbox_aggregate_version",
        "uq_claims_inbox_consumer_event",
    ):
        assert constraint in source, constraint


def test_there_is_no_return_window_table() -> None:
    """CLM-06 — the decision at the door is final, so there is no state to keep."""
    assert not any(
        "return" in table.name for table in Base.metadata.sorted_tables
    )
