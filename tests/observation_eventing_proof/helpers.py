"""Docker Compose, Alembic, NATS, and probe helpers for the eventing proof lab."""

from __future__ import annotations

import base64
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
    INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    NATS_SERVICE,
    NETWORK_NAME,
    OWNER_DATABASE,
    OWNER_PASSWORD,
    OWNER_USER,
    POSTGRES_SERVICE,
    VOLUME_JS_NAME,
    VOLUME_PG_NAME,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = REPO_ROOT / "infra" / "labs" / "observation-eventing-proof"
COMPOSE_FILE = LAB_ROOT / "compose.yaml"
BRIDGE_SERVICE = REPO_ROOT / "services" / "legacy_event_bridge"
AUDIT_SERVICE = REPO_ROOT / "services" / "audit"
PROBES_DIR = Path(__file__).resolve().parent / "probes"


class _PortCache:
    postgres: int | None = None
    nats: int | None = None


_port_cache = _PortCache()


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


def assert_lab_nats_url(url: str) -> None:
    lowered = url.lower()
    for fragment in FORBIDDEN_URL_FRAGMENTS:
        if fragment in lowered:
            msg = f"refusing non-lab NATS URL fragment: {fragment}"
            raise AssertionError(msg)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in ALLOWED_HOSTS:
        msg = f"refusing non-loopback NATS host: {host!r}"
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


def build_nats_url(*, port: int, host: str = "127.0.0.1") -> str:
    url = f"nats://{host}:{port}"
    assert_lab_nats_url(url)
    return url


def discover_host_port(service: str, container_port: int, *, force_refresh: bool = False) -> int:
    if service == POSTGRES_SERVICE and not force_refresh and _port_cache.postgres is not None:
        _wait_for_tcp_port("127.0.0.1", _port_cache.postgres)
        return _port_cache.postgres
    if service == NATS_SERVICE and not force_refresh and _port_cache.nats is not None:
        _wait_for_tcp_port("127.0.0.1", _port_cache.nats)
        return _port_cache.nats

    result = compose("port", service, str(container_port))
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"failed to discover {service} port: {msg}")
    binding = result.stdout.strip().splitlines()[-1].strip()
    host, _, port_text = binding.rpartition(":")
    if host not in ALLOWED_HOSTS:
        msg = f"{service} published on unexpected host: {host!r}"
        raise AssertionError(msg)
    port = int(port_text)
    _wait_for_tcp_port(host, port)
    if service == POSTGRES_SERVICE:
        _port_cache.postgres = port
    elif service == NATS_SERVICE:
        _port_cache.nats = port
    return port


def _wait_for_tcp_port(host: str, port: int, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    msg = f"host port not reachable at {host}:{port}"
    raise AssertionError(msg)


def _wait_for_lab_databases() -> None:
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
                "SELECT COUNT(*) FROM pg_database WHERE datname IN ('bridge_db', 'audit_db');",
                database=OWNER_DATABASE,
            )
            if probe == "2":
                return
        time.sleep(1)
    msg = "postgres lab databases not ready after timeout"
    raise AssertionError(msg)


def _run_topology_bootstrap() -> None:
    result = compose("run", "--rm", "topology-bootstrap")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HUDHUD_OBSERVATION_EVENTING_TOPOLOGY_BOOTSTRAPPED" in result.stdout


def compose_up() -> tuple[int, int]:
    _port_cache.postgres = None
    _port_cache.nats = None
    down = compose("down", "-v", "--remove-orphans")
    assert down.returncode == 0, down.stderr
    up = compose("up", "-d", POSTGRES_SERVICE, NATS_SERVICE)
    assert up.returncode == 0, up.stdout + up.stderr
    _wait_for_lab_databases()
    nats_health = compose(
        "exec",
        "-T",
        NATS_SERVICE,
        "wget",
        "-qO-",
        "http://127.0.0.1:8222/healthz",
    )
    assert nats_health.returncode == 0, nats_health.stderr
    _run_topology_bootstrap()
    postgres_port = discover_host_port(POSTGRES_SERVICE, 5432)
    nats_port = discover_host_port(NATS_SERVICE, 4222)
    return postgres_port, nats_port


def compose_down() -> None:
    result = compose("down", "-v", "--remove-orphans")
    assert result.returncode == 0, result.stderr
    _port_cache.postgres = None
    _port_cache.nats = None


def dedicated_resources_absent() -> bool:
    checks = [
        subprocess.run(
            ["docker", "network", "inspect", NETWORK_NAME],
            capture_output=True,
            check=False,
        ),
        subprocess.run(
            ["docker", "volume", "inspect", VOLUME_PG_NAME],
            capture_output=True,
            check=False,
        ),
        subprocess.run(
            ["docker", "volume", "inspect", VOLUME_JS_NAME],
            capture_output=True,
            check=False,
        ),
    ]
    return all(item.returncode != 0 for item in checks)


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


def table_count(database: str, table: str) -> int:
    return int(psql(f"SELECT COUNT(*) FROM {table};", database=database))


def grant_service_role_privileges(*, database: str, role: str) -> None:
    psql(
        f"GRANT USAGE ON SCHEMA public TO {role}; "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}; "
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role};",
        database=database,
    )


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
    database = _database_from_url(database_url)
    _ = service_dir
    return psql("SELECT version_num FROM alembic_version;", database=database)


def prepare_service_databases(postgres_port: int) -> None:
    bridge_owner = bridge_owner_url(postgres_port)
    audit_owner = audit_owner_url(postgres_port)
    alembic_upgrade_head(BRIDGE_SERVICE, bridge_owner)
    alembic_upgrade_head(AUDIT_SERVICE, audit_owner)
    grant_service_role_privileges(database=BRIDGE_DATABASE, role=BRIDGE_ROLE)
    grant_service_role_privileges(database=AUDIT_DATABASE, role=AUDIT_ROLE)


def bridge_owner_url(port: int) -> str:
    return build_database_url(database=BRIDGE_DATABASE, port=port)


def bridge_service_url(port: int) -> str:
    return build_database_url(
        database=BRIDGE_DATABASE,
        user=BRIDGE_ROLE,
        password=BRIDGE_ROLE_PASSWORD,
        port=port,
    )


def audit_owner_url(port: int) -> str:
    return build_database_url(database=AUDIT_DATABASE, port=port)


def audit_service_url(port: int) -> str:
    return build_database_url(
        database=AUDIT_DATABASE,
        user=AUDIT_ROLE,
        password=AUDIT_ROLE_PASSWORD,
        port=port,
    )


def run_bridge_outbox_publish(
    *,
    database_url: str,
    nats_url: str,
    envelope_json: str,
) -> dict[str, object]:
    probe = PROBES_DIR / "bridge_outbox_publish.py"
    result = subprocess.run(
        ["uv", "run", "python", str(probe)],
        cwd=BRIDGE_SERVICE,
        env=_service_subprocess_env(
            database_url,
            extra={
                "NATS_URL": nats_url,
                "ENVELOPE_JSON": envelope_json,
            },
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"bridge publish probe failed: {msg}")
    return json.loads(result.stdout.strip())


def run_audit_poll_once(
    *,
    database_url: str,
    nats_url: str,
    fail_first_ack: bool = False,
) -> dict[str, object]:
    probe = PROBES_DIR / "audit_poll_once.py"
    extra: dict[str, str] = {"NATS_URL": nats_url}
    if fail_first_ack:
        extra["AUDIT_FAIL_FIRST_ACK"] = "1"
    result = subprocess.run(
        ["uv", "run", "python", str(probe)],
        cwd=AUDIT_SERVICE,
        env=_service_subprocess_env(database_url, extra=extra),
        capture_output=True,
        text=True,
        check=False,
        timeout=INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"audit poll probe failed: {msg}")
    return json.loads(result.stdout.strip())


def run_jetstream_publish_raw(
    *,
    nats_url: str,
    subject: str,
    payload: bytes,
    msg_id: str | None = None,
) -> dict[str, object]:
    probe = PROBES_DIR / "jetstream_publish_raw.py"
    extra: dict[str, str] = {
        "NATS_URL": nats_url,
        "PUBLISH_SUBJECT": subject,
        "PUBLISH_PAYLOAD_B64": _b64(payload),
    }
    if msg_id is not None:
        extra["PUBLISH_MSG_ID"] = msg_id
    result = subprocess.run(
        ["uv", "run", "python", str(probe)],
        cwd=REPO_ROOT,
        env=_root_subprocess_env(extra),
        capture_output=True,
        text=True,
        check=False,
        timeout=INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"jetstream publish probe failed: {msg}")
    return json.loads(result.stdout.strip())


def _database_from_url(database_url: str) -> str:
    return urlparse(database_url).path.lstrip("/")


def _service_subprocess_env(
    database_url: str | None = None,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    if extra:
        env.update(extra)
    return env


def _root_subprocess_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.update(extra)
    return env


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def inbox_status_for_event(database: str, event_id: str) -> str | None:
    value = psql(
        "SELECT status FROM audit_integration_inbox "
        f"WHERE consumer_name = 'audit_bridge_entry_v1' AND event_id = '{event_id}';",
        database=database,
    )
    return value or None


def inbox_error_code_for_event(database: str, event_id: str) -> str | None:
    value = psql(
        "SELECT last_error_code FROM audit_integration_inbox "
        f"WHERE consumer_name = 'audit_bridge_entry_v1' AND event_id = '{event_id}';",
        database=database,
    )
    return value or None


def observation_exists(database: str, event_id: str) -> bool:
    count = psql(
        f"SELECT COUNT(*) FROM legacy_audit_observations WHERE event_id = '{event_id}';",
        database=database,
    )
    return int(count) == 1


def outbox_status_for_event(database: str, event_id: str) -> str:
    return psql(
        "SELECT status FROM bridge_integration_outbox "
        f"WHERE event_id = '{event_id}';",
        database=database,
    )
