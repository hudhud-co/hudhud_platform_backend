"""Static guards on the Notification schema and its migration.

The most important assertions here are about absence: no column that could hold a phone
number, a rendered body, or a delivery code.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from notification.domain.entities import (
    CentreEntry,
    ChannelPreference,
    Notification,
    RecipientProfile,
)
from notification.domain.messaging import InboxRecord
from notification.infrastructure.persistence.models import (
    Base,
    CentreEntryRow,
    ChannelPreferenceRow,
    IntegrationInboxRow,
    NotificationRow,
    RecipientProfileRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

_PAIRS = (
    (RecipientProfile, RecipientProfileRow, {"reachability"}),
    (ChannelPreference, ChannelPreferenceRow, set()),
    (Notification, NotificationRow, set()),
    (CentreEntry, CentreEntryRow, set()),
    (InboxRecord, IntegrationInboxRow, set()),
)


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_reachability_is_flattened_into_two_flags() -> None:
    columns = set(RecipientProfileRow.__table__.columns.keys())
    assert {"app_installed", "whatsapp_available"} <= columns


# --------------------------------------------------- what is not stored


def test_no_table_has_a_column_that_could_hold_a_phone_number() -> None:
    """Only the last four digits, and a keyed digest, ever land in this database."""
    suspicious = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if "phone" in column.name and column.name != "phone_last4"
    ]
    assert suspicious == []


def test_no_table_has_a_column_that_could_hold_a_delivery_code() -> None:
    # `last_error_code` is a failure classification, `tracking_code` is public, and the
    # other two name a template and an idempotency key. None is a secret.
    allowed = {"tracking_code", "template_code", "dedupe_key", "last_error_code"}
    suspicious = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if ("code" in column.name or "otp" in column.name) and column.name not in allowed
    ]
    assert suspicious == []


def test_the_notifications_table_stores_no_rendered_body() -> None:
    """A stored body would be a second place the delivery code lives."""
    assert "body" not in NotificationRow.__table__.columns


def test_the_recipient_key_column_is_a_digest_width() -> None:
    column = RecipientProfileRow.__table__.columns["recipient_key"]
    assert column.type.length == 64


# --------------------------------------------------- invariants in the schema


def test_the_recipient_key_is_checked_to_be_a_digest() -> None:
    assert "ck_notification_recipient_key_is_digest" in _migration_source()


def test_only_the_last_four_digits_fit_in_the_display_column() -> None:
    source = _migration_source()
    assert "ck_notification_recipient_last4" in source
    assert "length(phone_last4) = 4" in source


def test_the_acceptance_sms_cannot_be_opted_out_of_in_the_database() -> None:
    """NTF-03, enforced below the service so a direct write cannot create the opt-out."""
    source = _migration_source()
    assert "ck_notification_acceptance_sms_always_on" in source
    assert "category = 'ACCEPTANCE' AND channel = 'SMS' AND enabled = false" in source


def test_one_message_per_triggering_fact_recipient_and_channel() -> None:
    assert "uq_notification_dedupe_key" in _migration_source()


def test_a_failed_notification_always_records_why() -> None:
    assert "ck_notification_failure_has_reason" in _migration_source()


def test_one_preference_per_principal_category_and_channel() -> None:
    assert "uq_notification_preference_scope" in _migration_source()


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert "uq_notification_inbox_consumer_event" in _migration_source()


def test_there_is_no_outbox_table() -> None:
    """Notification consumes journey facts and publishes none."""
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
