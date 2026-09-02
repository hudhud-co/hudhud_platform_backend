"""Docker Compose, Alembic, and PostgreSQL helpers for the service proof lab."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from .constants import (
    ALLOWED_DATABASES,
    ALLOWED_HOSTS,
    AUDIT_DATABASE,
    AUDIT_ROLE,
    AUDIT_ROLE_PASSWORD,
    BRIDGE_DATABASE,
    BRIDGE_ROLE,
    BRIDGE_ROLE_PASSWORD,
    COMPOSE_PROFILE,
    COMPOSE_PROJECT,
    FORBIDDEN_URL_FRAGMENTS,
    LAB_DATABASES,
    NETWORK_NAME,
    OWNER_DATABASE,
    OWNER_PASSWORD,
    OWNER_USER,
    PICKUP_DATABASE,
    PICKUP_ROLE,
    PICKUP_ROLE_PASSWORD,
    POSTGRES_SERVICE,
    SHIPMENT_DATABASE,
    SHIPMENT_ROLE,
    SHIPMENT_ROLE_PASSWORD,
    VOLUME_NAME,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = REPO_ROOT / "infra" / "labs" / "service-postgres-proof"
COMPOSE_FILE = LAB_ROOT / "compose.yaml"
BRIDGE_SERVICE = REPO_ROOT / "services" / "legacy_event_bridge"
AUDIT_SERVICE = REPO_ROOT / "services" / "audit"
SHIPMENT_SERVICE = REPO_ROOT / "services" / "shipment"
PICKUP_SERVICE = REPO_ROOT / "services" / "pickup"

class _HostPortCache:
    port: int | None = None


_host_port_cache = _HostPortCache()


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


def assert_lab_database_url(url: str, *, expected_database: str) -> None:
    lowered = url.lower()
    for fragment in FORBIDDEN_URL_FRAGMENTS:
        if fragment in lowered:
            msg = f"refusing non-lab database URL fragment: {fragment}"
            raise AssertionError(msg)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in ALLOWED_HOSTS:
        msg = f"refusing non-loopback host: {host!r}"
        raise AssertionError(msg)
    database = parsed.path.lstrip("/")
    if database not in ALLOWED_DATABASES:
        msg = f"refusing unexpected database: {database!r}"
        raise AssertionError(msg)
    if database != expected_database:
        msg = f"expected database {expected_database!r}, got {database!r}"
        raise AssertionError(msg)


def build_database_url(
    *,
    database: str,
    user: str = OWNER_USER,
    password: str = OWNER_PASSWORD,
    host: str = "127.0.0.1",
    port: int,
) -> str:
    url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
    assert_lab_database_url(url, expected_database=database)
    return url


def discover_host_port(*, force_refresh: bool = False) -> int:
    if not force_refresh and _host_port_cache.port is not None:
        _wait_for_tcp_port("127.0.0.1", _host_port_cache.port)
        return _host_port_cache.port

    result = compose("port", POSTGRES_SERVICE, "5432")
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"failed to discover postgres port: {msg}")
    binding = result.stdout.strip().splitlines()[-1].strip()
    host, _, port_text = binding.rpartition(":")
    if host not in ALLOWED_HOSTS:
        msg = f"postgres published on unexpected host: {host!r}"
        raise AssertionError(msg)
    port = int(port_text)
    _wait_for_tcp_port(host, port)
    _host_port_cache.port = port
    return port


def _wait_for_tcp_port(host: str, port: int, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    msg = f"postgres host port not reachable at {host}:{port}"
    raise AssertionError(msg)


def _wait_for_lab_databases() -> None:
    database_list = ", ".join(f"'{name}'" for name in sorted(LAB_DATABASES))
    for _ in range(40):
        ready = compose(
            "exec",
            "-T",
            POSTGRES_SERVICE,
            "pg_isready",
            "-U",
            OWNER_USER,
            "-d",
            OWNER_DATABASE,
        )
        if ready.returncode == 0:
            probe = psql(
                f"SELECT COUNT(*) FROM pg_database WHERE datname IN ({database_list});",
                database=OWNER_DATABASE,
            )
            if probe == str(len(LAB_DATABASES)):
                return
        time.sleep(1)
    msg = "postgres lab databases not ready after timeout"
    raise AssertionError(msg)


def compose_up() -> int:
    _host_port_cache.port = None
    down = compose("down", "-v", "--remove-orphans")
    assert down.returncode == 0, down.stderr
    up = compose("up", "-d", POSTGRES_SERVICE)
    assert up.returncode == 0, up.stdout + up.stderr
    _wait_for_lab_databases()
    return discover_host_port()


def compose_down() -> None:
    result = compose("down", "-v", "--remove-orphans")
    assert result.returncode == 0, result.stderr
    _host_port_cache.port = None


def compose_restart_postgres() -> None:
    stop = compose("stop", POSTGRES_SERVICE)
    assert stop.returncode == 0, stop.stderr
    start = compose("start", POSTGRES_SERVICE)
    assert start.returncode == 0, start.stderr
    _wait_for_lab_databases()
    discover_host_port(force_refresh=True)


def dedicated_resources_absent() -> bool:
    net = subprocess.run(
        ["docker", "network", "inspect", NETWORK_NAME],
        capture_output=True,
        check=False,
    )
    vol = subprocess.run(
        ["docker", "volume", "inspect", VOLUME_NAME],
        capture_output=True,
        check=False,
    )
    return net.returncode != 0 and vol.returncode != 0


def psql(
    sql: str,
    *,
    user: str = OWNER_USER,
    database: str = OWNER_DATABASE,
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
        database,
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


def psql_rows(sql: str, *, user: str = OWNER_USER, database: str = OWNER_DATABASE) -> list[str]:
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
        database,
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
    rows: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("("):
            rows.append(stripped)
    return rows


def psql_expect_failure(sql: str, *, user: str, database: str) -> None:
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
        database,
        "-c",
        sql,
    ]
    result = compose(*command)
    assert result.returncode != 0, f"expected failure for {user}@{database}: {sql}"


def grant_service_role_privileges(*, database: str, role: str) -> None:
    psql(
        f"GRANT USAGE ON SCHEMA public TO {role}; "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}; "
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role};",
        database=database,
    )


def alembic_heads(service_dir: Path, database_url: str) -> list[str]:
    assert_lab_database_url(database_url, expected_database=_database_from_url(database_url))
    script = (
        "from alembic.config import Config\n"
        "from alembic.script import ScriptDirectory\n"
        "cfg = Config('alembic.ini')\n"
        f"cfg.set_main_option('sqlalchemy.url', {database_url!r})\n"
        "heads = ScriptDirectory.from_config(cfg).get_heads()\n"
        "print(','.join(heads))\n"
    )
    result = subprocess.run(
        ["uv", "run", "python", "-c", script],
        cwd=service_dir,
        env=_service_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"alembic heads failed: {msg}")
    output = result.stdout.strip()
    return [part for part in output.split(",") if part]


def alembic_upgrade_head(service_dir: Path, database_url: str) -> None:
    assert_lab_database_url(database_url, expected_database=_database_from_url(database_url))
    script = (
        "from alembic.config import Config\n"
        "from alembic import command\n"
        "cfg = Config('alembic.ini')\n"
        f"cfg.set_main_option('sqlalchemy.url', {database_url!r})\n"
        "command.upgrade(cfg, 'head')\n"
    )
    result = subprocess.run(
        ["uv", "run", "python", "-c", script],
        cwd=service_dir,
        env=_service_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"alembic upgrade head failed: {msg}")


def alembic_current_revision(service_dir: Path, database_url: str) -> str:
    assert_lab_database_url(database_url, expected_database=_database_from_url(database_url))
    database = _database_from_url(database_url)
    return psql("SELECT version_num FROM alembic_version;", database=database)


def list_public_tables(database: str) -> set[str]:
    rows = psql_rows(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;",
        database=database,
    )
    return {row.split("|", 1)[0] for row in rows if row.split("|", 1)[0] != "alembic_version"}


def list_service_tables(database: str, expected: frozenset[str]) -> set[str]:
    tables = list_public_tables(database)
    assert tables <= expected, tables
    return tables


def table_count(database: str, table: str) -> int:
    return int(psql(f"SELECT COUNT(*) FROM {table};", database=database))


def fetch_jsonb_column_type(database: str, table: str, column: str) -> str:
    value = psql(
        "SELECT data_type FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = '{table}' AND column_name = '{column}';",
        database=database,
    )
    return value


def fetch_timestamp_columns(database: str, table: str) -> set[str]:
    rows = psql_rows(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = "
        f"'{table}' AND data_type = 'timestamp with time zone';",
        database=database,
    )
    return {row.split("|", 1)[0] for row in rows}


def fetch_unique_constraints(database: str) -> set[str]:
    rows = psql_rows(
        "SELECT conname FROM pg_constraint "
        "WHERE contype = 'u' AND connamespace = 'public'::regnamespace;",
        database=database,
    )
    return {row.split("|", 1)[0] for row in rows}


def fetch_indexes(database: str) -> set[str]:
    rows = psql_rows(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public';",
        database=database,
    )
    return {row.split("|", 1)[0] for row in rows}


def fetch_foreign_key_count(database: str) -> int:
    return int(
        psql(
            "SELECT COUNT(*) FROM information_schema.table_constraints "
            "WHERE table_schema = 'public' AND constraint_type = 'FOREIGN KEY';",
            database=database,
        )
    )


def fetch_partial_index_predicates(database: str) -> dict[str, str]:
    rows = psql_rows(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public';",
        database=database,
    )
    predicates: dict[str, str] = {}
    for row in rows:
        name, _, definition = row.partition("|")
        if " WHERE " in definition:
            predicates[name] = definition.split(" WHERE ", 1)[1]
    return predicates


def run_bridge_transaction_probe(database_url: str) -> dict[str, object]:
    assert_lab_database_url(database_url, expected_database=BRIDGE_DATABASE)
    probe = Path(__file__).resolve().parent / "probes" / "bridge_transactions.py"
    return _run_probe(probe, BRIDGE_SERVICE, database_url)


def run_audit_transaction_probe(database_url: str) -> dict[str, object]:
    assert_lab_database_url(database_url, expected_database=AUDIT_DATABASE)
    probe = Path(__file__).resolve().parent / "probes" / "audit_transactions.py"
    return _run_probe(probe, AUDIT_SERVICE, database_url)


def run_shipment_transaction_probe(database_url: str) -> dict[str, object]:
    assert_lab_database_url(database_url, expected_database=SHIPMENT_DATABASE)
    probe = Path(__file__).resolve().parent / "probes" / "shipment_transactions.py"
    return _run_probe(probe, SHIPMENT_SERVICE, database_url)


def run_pickup_transaction_probe(database_url: str) -> dict[str, object]:
    assert_lab_database_url(database_url, expected_database=PICKUP_DATABASE)
    probe = Path(__file__).resolve().parent / "probes" / "pickup_transactions.py"
    return _run_probe(probe, PICKUP_SERVICE, database_url)


def _run_probe(probe: Path, service_dir: Path, database_url: str) -> dict[str, object]:
    result = subprocess.run(
        ["uv", "run", "python", str(probe)],
        cwd=service_dir,
        env=_service_subprocess_env(database_url),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"transaction probe failed: {msg}")
    return json.loads(result.stdout.strip())


def _database_from_url(database_url: str) -> str:
    return urlparse(database_url).path.lstrip("/")


def bridge_owner_url(port: int | None = None) -> str:
    return build_database_url(database=BRIDGE_DATABASE, port=port or discover_host_port())


def bridge_service_url(port: int | None = None) -> str:
    return build_database_url(
        database=BRIDGE_DATABASE,
        user=BRIDGE_ROLE,
        password=BRIDGE_ROLE_PASSWORD,
        port=port or discover_host_port(),
    )


def audit_owner_url(port: int | None = None) -> str:
    return build_database_url(database=AUDIT_DATABASE, port=port or discover_host_port())


def audit_service_url(port: int | None = None) -> str:
    return build_database_url(
        database=AUDIT_DATABASE,
        user=AUDIT_ROLE,
        password=AUDIT_ROLE_PASSWORD,
        port=port or discover_host_port(),
    )


def shipment_owner_url(port: int | None = None) -> str:
    return build_database_url(database=SHIPMENT_DATABASE, port=port or discover_host_port())


def shipment_service_url(port: int | None = None) -> str:
    return build_database_url(
        database=SHIPMENT_DATABASE,
        user=SHIPMENT_ROLE,
        password=SHIPMENT_ROLE_PASSWORD,
        port=port or discover_host_port(),
    ).replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)


def shipment_alembic_url(port: int | None = None) -> str:
    return shipment_owner_url(port=port)


def pickup_owner_url(port: int | None = None) -> str:
    return build_database_url(database=PICKUP_DATABASE, port=port or discover_host_port())


def pickup_service_url(port: int | None = None) -> str:
    return build_database_url(
        database=PICKUP_DATABASE,
        user=PICKUP_ROLE,
        password=PICKUP_ROLE_PASSWORD,
        port=port or discover_host_port(),
    )


def _service_subprocess_env(database_url: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    return env


def assert_single_head(service_dir: Path, database_url: str, expected_head: str) -> None:
    heads = alembic_heads(service_dir, database_url)
    assert len(heads) == 1, heads
    assert heads[0] == expected_head, heads
