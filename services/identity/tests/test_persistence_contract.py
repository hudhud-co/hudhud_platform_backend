"""Static guards on the Identity schema and its migration."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from identity.domain.entities import OtpChallenge, Principal, RoleGrant, Session
from identity.infrastructure.persistence.models import (
    Base,
    OtpChallengeRow,
    PrincipalRow,
    RoleGrantRow,
    SessionRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

_PAIRS = (
    (Principal, PrincipalRow),
    (OtpChallenge, OtpChallengeRow),
    (Session, SessionRow),
    (RoleGrant, RoleGrantRow),
)


def test_every_entity_field_has_a_column() -> None:
    """A new field that never reaches a column would silently vanish on write."""
    for entity_cls, row_cls in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)}
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_every_column_is_created_by_a_migration() -> None:
    source = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns.keys():  # noqa: SIM118
            if f'"{column}"' not in source:
                missing.append(f"{table.name}.{column}")
    assert missing == []


def test_every_table_is_created_by_a_migration() -> None:
    source = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))
    for table in Base.metadata.sorted_tables:
        assert f'"{table.name}"' in source


def test_the_schema_stores_no_recoverable_secret() -> None:
    """Phone, code, token and device id must exist only as keyed hashes."""
    forbidden = {"phone", "phone_number", "code", "otp_code", "token", "device_id"}
    for table in Base.metadata.sorted_tables:
        for column in table.columns.keys():  # noqa: SIM118
            assert column not in forbidden, f"{table.name}.{column}"


def test_one_live_code_per_phone_is_enforced_by_a_partial_unique_index() -> None:
    source = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))
    assert "uq_identity_otp_one_open_per_phone" in source
    assert "consumed_at IS NULL" in source


def test_one_live_role_grant_per_scope_is_enforced() -> None:
    source = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))
    assert "uq_identity_role_grant_live_scoped" in source
    assert "uq_identity_role_grant_live_global" in source


def test_the_migration_chain_has_exactly_one_root_and_one_head() -> None:
    revisions: dict[str, str | None] = {}
    for path in MIGRATIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = _value(text, "revision")
        down = _value(text, "down_revision")
        assert revision is not None, path.name
        revisions[revision] = down
    roots = [r for r, d in revisions.items() if d is None]
    heads = [r for r in revisions if r not in set(revisions.values())]
    assert len(roots) == 1, roots
    assert len(heads) == 1, heads


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    for path in MIGRATIONS.glob("*.py"):
        revision = _value(path.read_text(encoding="utf-8"), "revision")
        assert revision is not None and len(revision) <= 32, path.name


def _value(text: str, name: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(f"{name}: str"):
            raw = line.split("=", 1)[1].strip()
            if raw == "None":
                return None
            return raw.strip('"').strip("'")
    return None
