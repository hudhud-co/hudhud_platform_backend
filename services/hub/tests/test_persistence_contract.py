"""Static guards on the Hub schema and its migration."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from hub.domain.entities import (
    Consignment,
    DropOff,
    Hub,
    Linehaul,
    ParcelPresence,
    SealCheck,
    VehiclePosition,
)
from hub.domain.messaging import InboxRecord, OutboxRecord
from hub.infrastructure.persistence.models import (
    Base,
    ConsignmentRow,
    DropOffRow,
    HubRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    LinehaulRow,
    ParcelPresenceRow,
    SealCheckRow,
    VehiclePositionRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

_PAIRS = (
    (Hub, HubRow, {"cut_off"}),
    (DropOff, DropOffRow, set()),
    (ParcelPresence, ParcelPresenceRow, set()),
    (Consignment, ConsignmentRow, {"seal"}),
    (SealCheck, SealCheckRow, set()),
    (Linehaul, LinehaulRow, set()),
    (VehiclePosition, VehiclePositionRow, {"point"}),
    (OutboxRecord, IntegrationOutboxRow, set()),
    (InboxRecord, IntegrationInboxRow, set()),
)


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_the_cut_off_is_a_column_on_each_hub_not_a_constant() -> None:
    """v6.3 p.23 — "set per hub, not company-wide"."""
    assert "cut_off_local_time" in HubRow.__table__.columns
    assert HubRow.__table__.columns["cut_off_local_time"].nullable is False


def test_a_seal_is_flattened_into_code_time_and_actor() -> None:
    columns = set(ConsignmentRow.__table__.columns.keys())
    assert {
        "seal_code",
        "seal_applied_at",
        "seal_applied_by_actor_id",
    } <= columns


def test_a_position_keeps_both_coordinates_and_its_source() -> None:
    columns = set(VehiclePositionRow.__table__.columns.keys())
    assert {"latitude", "longitude", "source"} <= columns


# --------------------------------------------------- invariants in the schema


def test_an_accepted_drop_off_always_carries_its_label_weight_and_operator() -> None:
    source = _migration_source()
    assert "ck_hub_drop_off_accepted_is_complete" in source
    assert "accepted_by_actor_id IS NOT NULL" in source


def test_a_drop_off_weight_is_positive_when_present() -> None:
    assert "ck_hub_drop_off_weight_positive" in _migration_source()


def test_one_live_drop_off_per_tracking_code() -> None:
    source = _migration_source()
    assert "uq_hub_drop_off_live_tracking_code" in source
    assert "status IN ('EXPECTED', 'DETAILS_CAPTURED', 'LABELLED')" in source


def test_one_live_label_per_drop_off() -> None:
    assert "uq_hub_drop_off_label_code" in _migration_source()


def test_a_held_parcel_always_says_why() -> None:
    source = _migration_source()
    assert "ck_hub_presence_held_has_reason" in source
    assert "status <> 'HELD' OR hold_reason IS NOT NULL" in source


def test_a_parcel_appears_once_per_hub() -> None:
    assert "uq_hub_presence_parcel" in _migration_source()


def test_a_sealed_consignment_records_who_sealed_it_and_when() -> None:
    source = _migration_source()
    assert "ck_hub_consignment_seal_pair" in source
    assert "ck_hub_consignment_seal_attributed" in source


def test_a_seal_code_is_unique_across_live_consignments() -> None:
    assert "uq_hub_consignment_seal_code" in _migration_source()


def test_a_consignment_always_moves_between_two_different_hubs() -> None:
    """SHP-06 — the hub-to-hub stage exists only between hubs."""
    source = _migration_source()
    assert "ck_hub_consignment_between_two_hubs" in source
    assert "ck_hub_linehaul_between_two_hubs" in source


def test_one_linehaul_per_consignment() -> None:
    assert "uq_hub_linehaul_consignment" in _migration_source()


def test_one_event_per_aggregate_version() -> None:
    assert "uq_hub_outbox_aggregate_version" in _migration_source()


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert "uq_hub_inbox_consumer_event" in _migration_source()


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
