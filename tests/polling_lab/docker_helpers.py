"""Docker Compose and psql helpers for polling lab integration tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from uuid import UUID

from .conftest import COMPOSE_FILE, COMPOSE_PROFILE, COMPOSE_PROJECT, REPO_ROOT
from .strategies import Cursor, EventRow, poll_entities_updated_at_sql, poll_events_sql


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


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "-p",
            COMPOSE_PROJECT,
            "--profile",
            COMPOSE_PROFILE,
            *args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def compose_up() -> None:
    result = _compose("up", "-d", "postgres")
    assert result.returncode == 0, result.stderr
    for _ in range(30):
        wait = _compose(
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            "-U",
            "polling_lab",
            "-d",
            "polling_lab",
        )
        if wait.returncode == 0:
            probe = _compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "polling_lab",
                "-d",
                "polling_lab",
                "-t",
                "-A",
                "-c",
                "SELECT to_regclass('public.lab_events') IS NOT NULL;",
            )
            if probe.returncode == 0 and "t" in probe.stdout.lower():
                return
        time.sleep(1)
    msg = "postgres lab schema not ready after timeout"
    raise AssertionError(msg)


def compose_down() -> None:
    result = _compose("down", "-v", "--remove-orphans")
    assert result.returncode == 0, result.stderr


def psql(sql: str) -> str:
    result = _compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "polling_lab",
        "-d",
        "polling_lab",
        "-v",
        "ON_ERROR_STOP=1",
        "-t",
        "-A",
        "-F",
        ",",
        "-c",
        sql,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def psql_json(sql: str) -> list[dict]:
    wrapped = (
        "SELECT COALESCE(json_agg(row_to_json(q)), '[]'::json) FROM ("
        f"{sql.rstrip(';')}"
        ") q;"
    )
    raw = psql(wrapped)
    if not raw:
        return []
    return json.loads(raw)


def fetch_events(strategy: str, cursor: Cursor) -> list[EventRow]:
    if strategy == "updated_at_only":
        sql = poll_entities_updated_at_sql(cursor)
        rows = psql_json(sql)
        return [
            EventRow(
                row_id=UUID(row["id"]),
                occurred_at=row["updated_at"],
                event_type=row.get("name", ""),
            )
            for row in rows
        ]
    sql = poll_events_sql(strategy, cursor)
    rows = psql_json(sql)
    parsed: list[EventRow] = []
    for row in rows:
        parsed.append(
            EventRow(
                row_id=UUID(row["id"]),
                occurred_at=row["occurred_at"],
                event_type=row["event_type"],
                capture_seq=row.get("capture_seq"),
            )
        )
    return parsed


def load_persisted_cursor(strategy: str) -> Cursor:
    rows = psql_json(
        "SELECT cursor_ts, cursor_id::text AS cursor_id, cursor_seq, overlap_seconds "
        f"FROM lab_bridge_cursor WHERE stream_name = 'lab_events' AND strategy = '{strategy}' "
        "LIMIT 1"
    )
    if not rows:
        return Cursor()
    row = rows[0]
    return Cursor(
        ts=row.get("cursor_ts"),
        row_id=UUID(row["cursor_id"]) if row.get("cursor_id") else None,
        seq=row.get("cursor_seq"),
        overlap_seconds=row.get("overlap_seconds") or 0,
    )


def dedicated_resources_absent() -> bool:
    net = subprocess.run(
        ["docker", "network", "inspect", "hudhud_polling_lab"],
        capture_output=True,
        check=False,
    )
    vol = subprocess.run(
        ["docker", "volume", "inspect", "hudhud_polling_lab_pgdata"],
        capture_output=True,
        check=False,
    )
    return net.returncode != 0 and vol.returncode != 0
