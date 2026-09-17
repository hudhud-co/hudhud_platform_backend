"""Disposable PostgreSQL harness for proving a service's Alembic migration.

A migration that only ever runs against SQLite or against nothing at all is a guess. This
harness starts a throwaway PostgreSQL 16 container, applies the service's migration with
its own Alembic config and its own virtualenv, and then compares the schema PostgreSQL
actually built against the models the service will query through.

Everything is namespaced per service and torn down afterwards, so nothing here can touch a
developer's own database.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:16-alpine"
READY_TIMEOUT_SECONDS = 60
LAB_PASSWORD = "migration_proof_dev_only"


class DockerUnavailableError(RuntimeError):
    """Docker is not usable here — the proof is skipped rather than silently passing."""


def docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@dataclass(frozen=True, slots=True)
class PostgresLab:
    container: str
    port: int
    database: str

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg://postgres:{LAB_PASSWORD}"
            f"@127.0.0.1:{self.port}/{self.database}"
        )


def start_postgres(label: str) -> PostgresLab:
    """Start a disposable PostgreSQL. The caller must call `stop_postgres`."""
    container = f"hudhud-migration-proof-{label}-{secrets.token_hex(4)}"
    database = f"{label}_migration_proof"
    run = subprocess.run(
        [
            "docker", "run", "--rm", "--detach",
            "--name", container,
            "--publish", "127.0.0.1::5432",
            "--env", f"POSTGRES_PASSWORD={LAB_PASSWORD}",
            "--env", f"POSTGRES_DB={database}",
            # Disposable and never reused: a tmpfs data directory cannot outlive the test.
            "--tmpfs", "/var/lib/postgresql/data",
            POSTGRES_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        msg = f"could not start PostgreSQL: {run.stderr.strip()}"
        raise DockerUnavailableError(msg)

    port = _published_port(container)
    _wait_until_ready(container, database)
    return PostgresLab(container=container, port=port, database=database)


def stop_postgres(lab: PostgresLab) -> None:
    subprocess.run(
        ["docker", "rm", "--force", "--volumes", lab.container],
        capture_output=True,
        text=True,
        check=False,
    )


def _published_port(container: str) -> int:
    result = subprocess.run(
        ["docker", "port", container, "5432/tcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        msg = f"could not read published port: {result.stderr.strip()}"
        raise DockerUnavailableError(msg)
    return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[1])


def _wait_until_ready(container: str, database: str) -> None:
    """Ready means a TCP client can run a query — not that some socket answered.

    The postgres image starts a *temporary* server on a Unix socket while it initialises
    the data directory, then stops it and starts the real one. `pg_isready` over that
    socket answers yes to the temporary server, so a caller that trusts it connects over
    TCP a moment later and gets `FATAL: the database system is starting up`. The window
    is small on an idle machine and wide when several proofs start containers at once,
    which is exactly when the whole suite runs.

    Forcing TCP and requiring a real query closes it: the temporary server is not
    listening on 127.0.0.1, so this cannot pass until the server the test will actually
    use is serving.
    """
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last_error = "no probe completed"
    while time.monotonic() < deadline:
        probe = subprocess.run(
            [
                "docker", "exec",
                "--env", f"PGPASSWORD={LAB_PASSWORD}",
                container,
                "psql", "--host", "127.0.0.1", "--username", "postgres",
                "--dbname", database, "-tAc", "SELECT 1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "1":
            return
        last_error = (probe.stderr or probe.stdout).strip().splitlines()[-1:] or [""]
        last_error = last_error[0]
        time.sleep(0.5)
    msg = (
        f"PostgreSQL in {container} was not ready within {READY_TIMEOUT_SECONDS}s: "
        f"{last_error}"
    )
    raise DockerUnavailableError(msg)


def alembic(service: str, lab: PostgresLab, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the service's own Alembic, in the service's own virtualenv."""
    service_root = REPO_ROOT / "services" / service
    environment = {
        **os.environ,
        "SQLALCHEMY_URL": lab.url,
        f"{service.upper()}_DATABASE_URL": lab.url,
    }
    environment.pop("VIRTUAL_ENV", None)
    # Each service has its own virtualenv, but concurrent `uv run` calls still race on
    # uv's shared cache. The environments are built before any test runs.
    environment["UV_NO_SYNC"] = "1"
    return subprocess.run(
        [
            "uv", "run", "--project", str(service_root),
            "alembic", "-c", str(service_root / "alembic.ini"),
            "-x", f"sqlalchemy.url={lab.url}",
            *args,
        ],
        cwd=service_root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def run_in_service(service: str, script: str, lab: PostgresLab | None = None):
    """Run a script inside the service's own virtualenv and return the process.

    The migration proof reflects the schema; this lets a test go one step further and
    exercise the service's real unit of work against the database the migration just
    built, which is the only way to prove a version-conditional UPDATE or a rollback.
    """
    service_root = REPO_ROOT / "services" / service
    environment = {**os.environ}
    environment.pop("VIRTUAL_ENV", None)
    # Each service has its own virtualenv, but concurrent `uv run` calls still race on
    # uv's shared cache. The environments are built before any test runs.
    environment["UV_NO_SYNC"] = "1"
    if lab is not None:
        environment[f"{service.upper()}_DATABASE_URL"] = lab.url
    return subprocess.run(
        ["uv", "run", "--project", str(service_root), "python", "-c", script],
        cwd=service_root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def reflect(service: str, lab: PostgresLab) -> dict:
    """Read back what PostgreSQL built: tables, columns, indexes and check constraints."""
    service_root = REPO_ROOT / "services" / service
    script = f"""
import json
from sqlalchemy import create_engine, inspect

engine = create_engine({lab.url!r})
inspector = inspect(engine)
schema = {{}}
for table in sorted(inspector.get_table_names()):
    schema[table] = {{
        "columns": {{
            column["name"]: {{
                "nullable": bool(column["nullable"]),
                "type": str(column["type"]),
            }}
            for column in inspector.get_columns(table)
        }},
        "indexes": {{
            index["name"]: {{
                "unique": bool(index["unique"]),
                "columns": list(index["column_names"]),
            }}
            for index in inspector.get_indexes(table)
        }},
        "unique_constraints": sorted(
            constraint["name"] for constraint in inspector.get_unique_constraints(table)
        ),
        "check_constraints": sorted(
            constraint["name"] for constraint in inspector.get_check_constraints(table)
        ),
        "primary_key": list(inspector.get_pk_constraint(table)["constrained_columns"]),
    }}
print("SCHEMA_JSON:" + json.dumps(schema))
"""
    environment = {**os.environ}
    environment.pop("VIRTUAL_ENV", None)
    # Each service has its own virtualenv, but concurrent `uv run` calls still race on
    # uv's shared cache. The environments are built before any test runs.
    environment["UV_NO_SYNC"] = "1"
    result = subprocess.run(
        ["uv", "run", "--project", str(service_root), "python", "-c", script],
        cwd=service_root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        msg = f"schema reflection failed: {result.stderr.strip()[-2000:]}"
        raise RuntimeError(msg)
    for line in result.stdout.splitlines():
        if line.startswith("SCHEMA_JSON:"):
            return json.loads(line.removeprefix("SCHEMA_JSON:"))
    msg = "reflection produced no schema"
    raise RuntimeError(msg)


def model_tables(service: str, models_module: str) -> dict:
    """The tables the service's ORM expects, read from its own metadata."""
    service_root = REPO_ROOT / "services" / service
    script = f"""
import json
from {models_module} import Base

print("MODELS_JSON:" + json.dumps({{
    table.name: sorted(table.columns.keys())
    for table in Base.metadata.sorted_tables
}}))
"""
    environment = {**os.environ}
    environment.pop("VIRTUAL_ENV", None)
    # Each service has its own virtualenv, but concurrent `uv run` calls still race on
    # uv's shared cache. The environments are built before any test runs.
    environment["UV_NO_SYNC"] = "1"
    result = subprocess.run(
        ["uv", "run", "--project", str(service_root), "python", "-c", script],
        cwd=service_root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        msg = f"model metadata read failed: {result.stderr.strip()[-2000:]}"
        raise RuntimeError(msg)
    for line in result.stdout.splitlines():
        if line.startswith("MODELS_JSON:"):
            return json.loads(line.removeprefix("MODELS_JSON:"))
    msg = "model metadata produced nothing"
    raise RuntimeError(msg)
