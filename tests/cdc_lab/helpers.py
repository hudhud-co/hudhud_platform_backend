"""Shared paths and Docker helpers for the legacy CDC lab."""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = REPO_ROOT / "infra" / "labs" / "legacy-cdc"
COMPOSE_FILE = LAB_ROOT / "compose.yaml"
COMPOSE_PROFILE = "cdc-lab"
POSTGRES_SERVICE = "postgres"
DEFAULT_DB = "cdc_lab"
OWNER_USER = "cdc_lab_owner"
REPLICATOR_USER = "cdc_replicator"
WRITER_USER = "cdc_app_writer"
DEFAULT_PLUGIN = "test_decoding"

LSN_RE = re.compile(r"^0/[0-9A-Fa-f]+$")
SLOT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            COMPOSE_PROFILE,
            *args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def unique_slot(prefix: str = "hudhud_cdc_lab") -> str:
    suffix = uuid.uuid4().hex[:8]
    name = f"{prefix}_{suffix}"
    assert SLOT_NAME_RE.match(name)
    return name


@dataclass(frozen=True)
class WalChange:
    lsn: str
    data: str


@dataclass(frozen=True)
class SlotInfo:
    slot_name: str
    plugin: str
    restart_lsn: str | None
    confirmed_flush_lsn: str | None
    active: bool
    wal_status: str | None


class CdcLabClient:
    """Lab PostgreSQL client via docker compose exec (no host DB drivers)."""

    def psql(
        self,
        sql: str,
        *,
        user: str = OWNER_USER,
        db: str = DEFAULT_DB,
        tuples_only: bool = True,
    ) -> str:
        command = [
            "exec",
            "-T",
            POSTGRES_SERVICE,
            "psql",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            db,
        ]
        if tuples_only:
            command.extend(["-t", "-A"])
        command.extend(["-c", sql])
        result = compose(*command)
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"psql failed: {msg}\nSQL: {sql}")
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        data_lines = [line for line in lines if not line.startswith("INSERT ")]
        if not data_lines:
            return ""
        return data_lines[0]

    def psql_rows(
        self,
        sql: str,
        *,
        user: str = OWNER_USER,
        db: str = DEFAULT_DB,
    ) -> list[list[str]]:
        command = [
            "exec",
            "-T",
            POSTGRES_SERVICE,
            "psql",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            db,
            "-t",
            "-A",
            "-F",
            "|",
            "-c",
            sql,
        ]
        result = compose(*command)
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"psql failed: {msg}\nSQL: {sql}")
        rows: list[list[str]] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("("):
                continue
            rows.append([part.strip() for part in stripped.split("|")])
        return rows

    def reset_probe_table(self) -> None:
        self.psql("TRUNCATE lab.capture_probe RESTART IDENTITY CASCADE;")

    def insert_probe(self, payload: str, *, user: str = WRITER_USER, amount: str = "0.00") -> int:
        escaped = payload.replace("'", "''")
        row_id = self.psql(
            (
                "INSERT INTO lab.capture_probe (payload, amount) "
                f"VALUES ('{escaped}', {amount}) RETURNING id;"
            ),
            user=user,
        )
        return int(row_id)

    def update_probe(self, row_id: int, payload: str, *, user: str = WRITER_USER) -> None:
        escaped = payload.replace("'", "''")
        self.psql(
            f"UPDATE lab.capture_probe SET payload = '{escaped}' WHERE id = {row_id};",
            user=user,
        )

    def delete_probe(self, row_id: int, *, user: str = WRITER_USER) -> None:
        self.psql(f"DELETE FROM lab.capture_probe WHERE id = {row_id};", user=user)

    def setting(self, name: str) -> str:
        return self.psql(f"SHOW {name};")

    def current_lsn(self) -> str:
        value = self.psql("SELECT pg_current_wal_lsn();")
        assert LSN_RE.match(value), value
        return value

    def create_slot(self, slot_name: str, plugin: str = DEFAULT_PLUGIN) -> str:
        lsn = self.psql(
            f"SELECT lsn FROM pg_create_logical_replication_slot('{slot_name}', '{plugin}');"
        )
        assert LSN_RE.match(lsn), lsn
        return lsn

    def drop_slot(self, slot_name: str) -> None:
        self.psql(f"SELECT pg_drop_replication_slot('{slot_name}');")

    def slot_exists(self, slot_name: str) -> bool:
        count = self.psql(
            f"SELECT COUNT(*) FROM pg_replication_slots WHERE slot_name = '{slot_name}';"
        )
        return int(count) == 1

    def slot_info(self, slot_name: str) -> SlotInfo:
        rows = self.psql_rows(
            "SELECT slot_name, plugin, restart_lsn::text, confirmed_flush_lsn::text, "
            "active, wal_status "
            "FROM pg_replication_slots "
            f"WHERE slot_name = '{slot_name}';"
        )
        assert len(rows) == 1, rows
        row = rows[0]
        return SlotInfo(
            slot_name=row[0],
            plugin=row[1],
            restart_lsn=row[2] if row[2] else None,
            confirmed_flush_lsn=row[3] if row[3] else None,
            active=row[4].lower() == "t",
            wal_status=row[5] if len(row) > 5 and row[5] else None,
        )

    def get_changes(
        self,
        slot_name: str,
        *,
        upto_lsn: str | None = None,
        limit: int | None = None,
    ) -> list[WalChange]:
        upto = f"'{upto_lsn}'::pg_lsn" if upto_lsn else "NULL"
        max_changes = str(limit) if limit is not None else "NULL"
        rows = self.psql_rows(
            f"SELECT lsn::text, data FROM pg_logical_slot_get_changes("
            f"'{slot_name}', {upto}, {max_changes});"
        )
        return [WalChange(lsn=row[0], data=row[1]) for row in rows if len(row) >= 2]

    def peek_changes(
        self,
        slot_name: str,
        *,
        upto_lsn: str | None = None,
        limit: int | None = None,
    ) -> list[WalChange]:
        upto = f"'{upto_lsn}'::pg_lsn" if upto_lsn else "NULL"
        max_changes = str(limit) if limit is not None else "NULL"
        rows = self.psql_rows(
            f"SELECT lsn::text, data FROM pg_logical_slot_peek_changes("
            f"'{slot_name}', {upto}, {max_changes});"
        )
        return [WalChange(lsn=row[0], data=row[1]) for row in rows if len(row) >= 2]

    def advance_slot(self, slot_name: str, lsn: str) -> None:
        self.psql(f"SELECT pg_replication_slot_advance('{slot_name}', '{lsn}'::pg_lsn);")

    def slot_lag_bytes(self, slot_name: str) -> int:
        value = self.psql(
            "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) "
            "FROM pg_replication_slots "
            f"WHERE slot_name = '{slot_name}';"
        )
        return int(value)

    def save_checkpoint(self, slot_name: str, lsn: str) -> None:
        self.psql(
            "INSERT INTO lab.bridge_checkpoint (slot_name, confirmed_lsn) "
            f"VALUES ('{slot_name}', '{lsn}'::pg_lsn) "
            "ON CONFLICT (slot_name) DO UPDATE "
            "SET confirmed_lsn = EXCLUDED.confirmed_lsn, updated_at = now();"
        )

    def load_checkpoint(self, slot_name: str) -> str | None:
        value = self.psql(
            "SELECT confirmed_lsn::text FROM lab.bridge_checkpoint "
            f"WHERE slot_name = '{slot_name}';"
        )
        return value or None

    def capture_hwm_snapshot(self) -> tuple[str, str, int]:
        rows = self.psql_rows(
            "SELECT snapshot_id, hwm_lsn, probe_count FROM lab.capture_hwm_snapshot();"
        )
        assert len(rows) == 1, rows
        return rows[0][0], rows[0][1], int(rows[0][2])

    def count_probes(self) -> int:
        return int(self.psql("SELECT COUNT(*) FROM lab.capture_probe;"))

    def run_sql_block(self, sql: str, *, user: str = OWNER_USER) -> str:
        return self.psql(sql, user=user, tuples_only=False)
