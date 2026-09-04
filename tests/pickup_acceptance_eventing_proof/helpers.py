"""Docker Compose, Alembic, NATS, and probe helpers for the acceptance eventing lab."""

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
    COMPOSE_PROFILE,
    COMPOSE_PROJECT,
    FORBIDDEN_URL_FRAGMENTS,
    INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    NATS_SERVICE,
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
    VOLUME_JS_NAME,
    VOLUME_PG_NAME,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = REPO_ROOT / "infra" / "labs" / "pickup-acceptance-eventing-proof"
COMPOSE_FILE = LAB_ROOT / "compose.yaml"
PICKUP_SERVICE = REPO_ROOT / "services" / "pickup"
SHIPMENT_SERVICE = REPO_ROOT / "services" / "shipment"
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
    msg = "host port not reachable"
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
                "SELECT COUNT(*) FROM pg_database "
                "WHERE datname IN ('pickup_db', 'shipment_db');",
                database=OWNER_DATABASE,
            )
            if probe == "2":
                return
        time.sleep(1)
    msg = "postgres lab databases not ready after timeout"
    raise AssertionError(msg)


def _run_topology_bootstrap(nats_url: str) -> None:
    script = LAB_ROOT / "scripts" / "bootstrap_pickup_topology.py"
    result = subprocess.run(
        ["uv", "run", "python", str(script)],
        cwd=REPO_ROOT,
        env=_root_subprocess_env({"NATS_URL": nats_url}),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HUDHUD_PICKUP_ACCEPTANCE_EVENTING_TOPOLOGY_BOOTSTRAPPED" in result.stdout


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
    postgres_port = discover_host_port(POSTGRES_SERVICE, 5432)
    nats_port = discover_host_port(NATS_SERVICE, 4222)
    _run_topology_bootstrap(build_nats_url(port=nats_port))
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
        subprocess.run(
            ["docker", "inspect", "hudhud-pickup-acceptance-eventing-proof-postgres"],
            capture_output=True,
            check=False,
        ),
        subprocess.run(
            ["docker", "inspect", "hudhud-pickup-acceptance-eventing-proof-nats"],
            capture_output=True,
            check=False,
        ),
    ]
    return all(item.returncode != 0 for item in checks)


def leftover_worker_processes() -> tuple[str, ...]:
    result = subprocess.run(
        ["ps", "-ax", "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ()
    needles = (
        "pickup.runtime.relay_main",
        "python -m pickup.runtime.relay_main",
        "python -m shipment.worker",
        "shipment.worker",
    )
    found: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "run_pipeline_proof.py" in stripped:
            continue
        if any(needle in stripped for needle in needles):
            found.append(stripped.split()[0])
    return tuple(found)


def leftover_pytest_processes() -> tuple[str, ...]:
    result = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ()
    self_pid = str(os.getpid())
    found: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "pytest" not in stripped:
            continue
        pid, _, command = stripped.partition(" ")
        if pid == self_pid:
            continue
        if "pickup_acceptance_eventing_proof" in command or "shipment.worker" in command:
            found.append(pid)
    return tuple(found)


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
        raise RuntimeError(f"psql failed: {msg}")
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


def alembic_current_revision(database: str) -> str:
    return psql("SELECT version_num FROM alembic_version;", database=database)


def prepare_service_databases(postgres_port: int) -> None:
    pickup_owner = pickup_owner_url(postgres_port)
    shipment_owner = shipment_owner_url(postgres_port)
    alembic_upgrade_head(PICKUP_SERVICE, pickup_owner)
    alembic_upgrade_head(SHIPMENT_SERVICE, shipment_owner)
    grant_service_role_privileges(database=PICKUP_DATABASE, role=PICKUP_ROLE)
    grant_service_role_privileges(database=SHIPMENT_DATABASE, role=SHIPMENT_ROLE)


def pickup_owner_url(port: int) -> str:
    return build_database_url(database=PICKUP_DATABASE, port=port)


def pickup_service_url(port: int) -> str:
    return build_database_url(
        database=PICKUP_DATABASE,
        user=PICKUP_ROLE,
        password=PICKUP_ROLE_PASSWORD,
        port=port,
    )


def shipment_owner_url(port: int) -> str:
    return build_database_url(database=SHIPMENT_DATABASE, port=port)


def shipment_service_url(port: int) -> str:
    return build_database_url(
        database=SHIPMENT_DATABASE,
        user=SHIPMENT_ROLE,
        password=SHIPMENT_ROLE_PASSWORD,
        port=port,
    )


def run_probe(
    *,
    probe_name: str,
    cwd: Path,
    database_url: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, object]:
    probe = PROBES_DIR / probe_name
    result = subprocess.run(
        ["uv", "run", "python", str(probe)],
        cwd=cwd,
        env=_service_subprocess_env(database_url, extra=extra),
        capture_output=True,
        text=True,
        check=False,
        timeout=INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{probe_name} failed: {msg}")
    return json.loads(result.stdout.strip())


def run_pickup_accept(
    *,
    database_url: str,
    shipment_id: str,
    driver_id: str,
    waybill: str,
    idempotency_key: str,
    outcome: str = "ACCEPTED",
    media_key: str | None = None,
) -> dict[str, object]:
    extra = {
        "SHIPMENT_ID": shipment_id,
        "DRIVER_ID": driver_id,
        "WAYBILL": waybill,
        "IDEMPOTENCY_KEY": idempotency_key,
        "OUTCOME": outcome,
    }
    if media_key is not None:
        extra["MEDIA_KEY"] = media_key
    return run_probe(
        probe_name="pickup_accept.py",
        cwd=PICKUP_SERVICE,
        database_url=database_url,
        extra=extra,
    )


def run_pickup_relay_once(*, database_url: str, nats_url: str) -> dict[str, object]:
    return run_probe(
        probe_name="pickup_relay_once.py",
        cwd=PICKUP_SERVICE,
        database_url=database_url,
        extra={
            "NATS_URL": nats_url,
            "PICKUP_RELAY_ENABLED": "true",
            "PICKUP_NATS_DEV_NO_AUTH": "true",
            "PICKUP_ENVIRONMENT": "local",
        },
    )


def run_shipment_seed(
    *,
    database_url: str,
    shipment_id: str,
    waybill: str,
) -> dict[str, object]:
    return run_probe(
        probe_name="shipment_seed.py",
        cwd=SHIPMENT_SERVICE,
        database_url=database_url,
        extra={"SHIPMENT_ID": shipment_id, "WAYBILL": waybill},
    )


def run_shipment_poll_once(
    *,
    database_url: str,
    nats_url: str,
    fail_first_ack: bool = False,
    fail_quarantine_persist: bool = False,
) -> dict[str, object]:
    extra = {
        "NATS_URL": nats_url,
        "SHIPMENT_NATS_URL": nats_url,
        "SHIPMENT_NATS_ENABLED": "true",
        "SHIPMENT_ALLOW_NO_AUTH_LOCAL": "true",
        "SHIPMENT_ENVIRONMENT": "local",
        "SHIPMENT_HANDLER_CONCURRENCY": "1",
        "SHIPMENT_PULL_BATCH_SIZE": "1",
    }
    if fail_first_ack:
        extra["SHIPMENT_FAIL_FIRST_ACK"] = "1"
    if fail_quarantine_persist:
        extra["SHIPMENT_FAIL_QUARANTINE_PERSIST"] = "1"
    return run_probe(
        probe_name="shipment_poll_once.py",
        cwd=SHIPMENT_SERVICE,
        database_url=database_url,
        extra=extra,
    )


def run_shipment_inspect(
    *,
    database_url: str,
    shipment_id: str,
    event_id: str,
    expected_custody_id: str,
) -> dict[str, object]:
    return run_probe(
        probe_name="shipment_inspect.py",
        cwd=SHIPMENT_SERVICE,
        database_url=database_url,
        extra={
            "SHIPMENT_ID": shipment_id,
            "EVENT_ID": event_id,
            "EXPECTED_CUSTODY_ID": expected_custody_id,
        },
    )


def run_outbox_republish(
    *,
    database_url: str,
    nats_url: str,
    event_id: str,
    msg_id: str,
) -> dict[str, object]:
    return run_probe(
        probe_name="outbox_republish.py",
        cwd=PICKUP_SERVICE,
        database_url=database_url,
        extra={
            "NATS_URL": nats_url,
            "EVENT_ID": event_id,
            "PUBLISH_MSG_ID": msg_id,
            "PICKUP_RELAY_ENABLED": "true",
            "PICKUP_NATS_DEV_NO_AUTH": "true",
            "PICKUP_ENVIRONMENT": "local",
        },
    )


def run_jetstream_publish_raw(
    *,
    nats_url: str,
    subject: str,
    payload: bytes,
    msg_id: str | None = None,
) -> dict[str, object]:
    extra: dict[str, str] = {
        "NATS_URL": nats_url,
        "PUBLISH_SUBJECT": subject,
        "PUBLISH_PAYLOAD_B64": _b64(payload),
    }
    if msg_id is not None:
        extra["PUBLISH_MSG_ID"] = msg_id
    result = subprocess.run(
        ["uv", "run", "python", str(PROBES_DIR / "jetstream_publish_raw.py")],
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


def outbox_status_for_event(event_id: str) -> str:
    return psql(
        "SELECT status FROM pickup_integration_outbox "
        f"WHERE event_id = '{event_id}';",
        database=PICKUP_DATABASE,
    )


def outbox_count() -> int:
    return table_count(PICKUP_DATABASE, "pickup_integration_outbox")


def inbox_status_for_event(event_id: str) -> str | None:
    value = psql(
        "SELECT status FROM shipment_integration_inbox "
        f"WHERE consumer_name = 'shipment_pickup_facts_v1' AND event_id = '{event_id}';",
        database=SHIPMENT_DATABASE,
    )
    return value or None


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
        env["SHIPMENT_DATABASE_URL"] = database_url
        env["PICKUP_DATABASE_URL"] = database_url
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
