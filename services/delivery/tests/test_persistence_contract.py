"""Static guards on the Delivery schema and its migration.

The mapping between an entity and its row is where a field quietly stops being persisted.
These tests walk both sides and name the flattened value objects explicitly, so adding a
field to an entity without a column fails here rather than in production.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from delivery.domain.entities import (
    CourierRating,
    DeliveryManifest,
    DeliveryStop,
    FailedAttempt,
    ParcelIssueReport,
    PaymentRecord,
    PhotoEvidence,
    ReceiverPreference,
    VerificationAttempt,
)
from delivery.domain.messaging import InboxRecord, OutboxRecord
from delivery.infrastructure.persistence.models import (
    Base,
    CourierRatingRow,
    DeliveryManifestRow,
    DeliveryStopRow,
    FailedAttemptRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    ParcelIssueReportRow,
    PaymentRecordRow,
    PhotoEvidenceRow,
    ReceiverPreferenceRow,
    VerificationAttemptRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Composite value objects are flattened into columns and asserted separately below.
_PAIRS = (
    (DeliveryManifest, DeliveryManifestRow, set()),
    (DeliveryStop, DeliveryStopRow, {"settings", "cod_amount"}),
    (VerificationAttempt, VerificationAttemptRow, set()),
    (PaymentRecord, PaymentRecordRow, {"amount", "pos_receipt"}),
    (PhotoEvidence, PhotoEvidenceRow, {"media"}),
    (FailedAttempt, FailedAttemptRow, set()),
    (ReceiverPreference, ReceiverPreferenceRow, {"window", "geo"}),
    (ParcelIssueReport, ParcelIssueReportRow, {"media"}),
    (CourierRating, CourierRatingRow, set()),
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


def test_the_parcel_settings_are_flattened_into_three_columns() -> None:
    columns = set(DeliveryStopRow.__table__.columns.keys())
    assert {
        "open_box_allowed",
        "photo_documentation",
        "packaging_seal_code",
    } <= columns


def test_money_is_flattened_into_minor_units_and_a_currency() -> None:
    """Exact IQD: never one column holding a formatted amount."""
    for row_cls, prefix in (
        (DeliveryStopRow, "cod_amount"),
        (PaymentRecordRow, "amount"),
    ):
        columns = set(row_cls.__table__.columns.keys())
        assert f"{prefix}_minor_units" in columns
        assert f"{prefix}_currency" in columns


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
    """A rating average is computed; nothing is stored as a float."""
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if "FLOAT" in str(column.type).upper() or "DOUBLE" in str(column.type).upper()
    ]
    assert offenders == []


def test_media_references_are_flattened_into_a_pointer() -> None:
    """Delivery holds a bucket and a key, never the bytes."""
    columns = set(PhotoEvidenceRow.__table__.columns.keys())
    assert {"media_bucket", "media_key", "media_content_type"} <= columns
    payment_columns = set(PaymentRecordRow.__table__.columns.keys())
    assert {"pos_receipt_bucket", "pos_receipt_key"} <= payment_columns


def test_no_column_holds_binary_content() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if "BLOB" in str(column.type).upper() or "BYTEA" in str(column.type).upper()
    ]
    assert offenders == []


def test_every_mutable_aggregate_carries_a_version() -> None:
    """Optimistic concurrency needs somewhere to keep the version."""
    for row_cls in (
        DeliveryManifestRow,
        DeliveryStopRow,
        FailedAttemptRow,
        ReceiverPreferenceRow,
        CourierRatingRow,
    ):
        assert "version" in row_cls.__table__.columns, row_cls.__name__


def test_append_only_records_carry_no_version() -> None:
    """A verification attempt or a photo is never edited, so a version would mislead."""
    for row_cls in (VerificationAttemptRow, PhotoEvidenceRow, PaymentRecordRow):
        assert "version" not in row_cls.__table__.columns, row_cls.__name__


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


def test_the_migration_is_expand_only() -> None:
    """W26 creates new tables and touches nothing that exists."""
    source = _migration_source()
    assert "op.drop_column" not in source
    assert "op.alter_column" not in source


def test_the_migration_declares_a_head_and_no_parent() -> None:
    source = _migration_source()
    assert 'revision: str = "w26_delivery_core_001"' in source
    assert "down_revision: str | Sequence[str] | None = None" in source


def test_the_revision_identifier_fits_alembics_column() -> None:
    for match in re.findall(r'revision: str = "([^"]+)"', _migration_source()):
        assert len(match) <= 32, match


def test_the_invariants_reach_the_database() -> None:
    """A rule enforced only in Python is a rule one direct UPDATE away from broken."""
    source = _migration_source()
    for constraint in (
        "ck_delivery_stop_delivered_is_verified",
        "ck_delivery_stop_closed_at_matches_status",
        "ck_delivery_stop_code_digest_is_a_digest",
        "ck_delivery_payment_pos_has_proof",
        "ck_delivery_failed_attempt_decision_is_attributed",
        "ck_delivery_rating_score_range",
        "uq_delivery_stop_one_live_per_parcel",
        "uq_delivery_manifest_one_open_per_driver",
        "uq_delivery_outbox_aggregate_version",
        "uq_delivery_inbox_consumer_event",
    ):
        assert constraint in source, constraint


def test_the_migration_carries_no_delivery_code_column() -> None:
    """DRV-L05 — in the DDL as well as in the models."""
    source = _migration_source()
    assert '"delivery_code"' not in source
    assert '"otp"' not in source


def test_the_migration_carries_no_id_photograph_column() -> None:
    """DRV-L07."""
    source = _migration_source()
    for forbidden in ('"id_photo', '"identity_document', '"id_image'):
        assert forbidden not in source, forbidden
