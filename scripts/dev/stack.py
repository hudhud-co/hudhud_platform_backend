#!/usr/bin/env python3
"""Run the HUDHUD platform locally, so the APIs can actually be used.

    uv run python scripts/dev/stack.py up       # start everything, print the URLs
    uv run python scripts/dev/stack.py status   # what is running, and is it ready
    uv run python scripts/dev/stack.py logs finance
    uv run python scripts/dev/stack.py down     # stop everything and remove the data

What it does, in order: start one PostgreSQL container and one NATS container, create a
database per service, run each service's own Alembic migrations against its own database,
then start each FastAPI app with `uvicorn` on its allocated port.

Two decisions worth knowing about:

* **A database per service, not a schema per service.** That is ADR-0011's rule and the
  thing a shared-database shortcut would quietly break. Each service only ever gets its
  own URL.
* **Every service is wired to Identity.** These services deny everything when Identity is
  unreachable, so a stack without it would look broken in a confusing way. The stack
  issues each service its own credential and passes it in.

The data lives in a container that `down` removes. Nothing here touches a developer's
own PostgreSQL.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = REPO_ROOT / ".dev-stack"
LOG_DIR = RUN_DIR / "logs"
STATE_FILE = RUN_DIR / "state.json"

POSTGRES_CONTAINER = "hudhud-dev-postgres"
NATS_CONTAINER = "hudhud-dev-nats"
POSTGRES_IMAGE = "postgres:16-alpine"
NATS_IMAGE = "nats:2.10.24-alpine"
POSTGRES_PORT = 55432
NATS_PORT = 54222
POSTGRES_PASSWORD = "hudhud_dev_only"

#: The shared secret services present to Identity's introspection endpoint. Local only:
#: production supplies a real per-service credential.
SERVICE_CREDENTIAL = "hudhud-dev-service-credential"

#: DRV-A06 and PAY-08 — numbers v6.3 never fixes. These are development values so the
#: stack runs; production gets the real ones from an accountant.
DEV_CASH_LIMIT = "1000000"
DEV_RETURN_TRIP_FEE = "5000"

#: DRV-L05 is an unresolved source conflict, so the stack leaves DELIVERY_CODE_LENGTH
#: unset on purpose. Delivery answers 501 for code verification and works for everything
#: else. Set it here only to try one of the two readings.
DELIVERY_CODE_LENGTH = os.environ.get("HUDHUD_DEV_DELIVERY_CODE_LENGTH", "")


@dataclass(frozen=True)
class Service:
    name: str
    port: int
    #: Extra environment beyond the database, Identity and NATS wiring.
    extra: dict[str, str] = field(default_factory=dict)
    #: Services that must be listening before this one is useful.
    needs: tuple[str, ...] = ()


#: Every service that calls Identity's introspection presents this name:secret pair.
#: One shared secret is fine locally; production issues one per service.
IDENTITY_SERVICE_CREDENTIALS = ",".join(
    f"{service}:{SERVICE_CREDENTIAL}"
    for service in (
        "customer", "merchant", "ordering", "hub", "notification", "workforce",
        "delivery", "finance", "pickup", "shipment", "tracking", "audit",
        "claims",
    )
)

#: The first principal to sign in with this phone is granted OPERATIONS, which is how a
#: fresh platform gets its first administrator. Everything else is granted from there.
BOOTSTRAP_OPERATIONS_PHONE = "+9647700000001"

SERVICES: tuple[Service, ...] = (
    Service(
        "identity",
        8101,
        extra={
            # Without a signing key Identity refuses to expose its routes at all — no
            # key means no code can be hashed, and issuing one would be unsafe. This is
            # a development key; production supplies a real one.
            "IDENTITY_SIGNING_KEY": "hudhud-dev-identity-signing-key",
            "IDENTITY_SERVICE_CREDENTIALS": IDENTITY_SERVICE_CREDENTIALS,
            # The OTP is printed to Identity's log rather than sent. Refused outside
            # local and test by `load_settings`, which is why this is safe to set here.
            "IDENTITY_OTP_DELIVERY_CHANNEL": "console",
            "IDENTITY_BOOTSTRAP_OPERATIONS_PHONE": BOOTSTRAP_OPERATIONS_PHONE,
        },
    ),
    Service("customer", 8102, needs=("identity",)),
    Service("merchant", 8103, needs=("identity",)),
    Service("ordering", 8104, needs=("identity", "merchant")),
    Service("hub", 8105, needs=("identity",)),
    Service("notification", 8106, needs=("identity",)),
    Service("workforce", 8107, needs=("identity",)),
    Service(
        "delivery",
        8108,
        needs=("identity", "workforce"),
        extra={
            "DELIVERY_WORKFORCE_BASE_URL": "http://127.0.0.1:8107",
            "DELIVERY_WORKFORCE_SERVICE_CREDENTIAL": SERVICE_CREDENTIAL,
            "DELIVERY_CODE_HMAC_KEY": "hudhud-dev-delivery-code-key",
            **({"DELIVERY_CODE_LENGTH": DELIVERY_CODE_LENGTH} if DELIVERY_CODE_LENGTH else {}),
        },
    ),
    Service(
        "finance",
        8109,
        needs=("identity",),
        extra={
            "FINANCE_DEFAULT_CASH_LIMIT": DEV_CASH_LIMIT,
            "FINANCE_RETURN_TRIP_FEE": DEV_RETURN_TRIP_FEE,
        },
    ),
    Service("pickup", 8110, needs=("identity",)),
    Service("shipment", 8111, needs=("identity",)),
    Service("tracking", 8112),
    Service("audit", 8113),
    # CLM-08 is an unresolved v6.3 Open Item, so CLAIMS_HIGH_VALUE_THRESHOLD is left
    # unset on purpose. Claims reports it as a readiness blocker and refuses only
    # `is_high_value`; filing, review, approval, rejection and the support thread all
    # work. Set it here only to try a candidate threshold.
    Service("claims", 8114, needs=("identity",)),
)

BY_NAME = {service.name: service for service in SERVICES}


# ------------------------------------------------------------------ plumbing


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, check=False, **kwargs
    )


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return run(["docker", "info", "--format", "{{.ServerVersion}}"]).returncode == 0


def database_url(service: str) -> str:
    return (
        f"postgresql+psycopg://postgres:{POSTGRES_PASSWORD}"
        f"@127.0.0.1:{POSTGRES_PORT}/{service}_db"
    )


def service_env(service: Service) -> dict[str, str]:
    upper = service.name.upper()
    env = {
        **os.environ,
        # Each service reads only its own URL. There is no shared database handle, and
        # no service is given another's.
        f"{upper}_DATABASE_URL": database_url(service.name),
        "DATABASE_URL": database_url(service.name),
        f"{upper}_ENVIRONMENT": "local",
        f"{upper}_NATS_URL": f"nats://127.0.0.1:{NATS_PORT}",
        f"{upper}_IDENTITY_BASE_URL": f"http://127.0.0.1:{BY_NAME['identity'].port}",
        f"{upper}_IDENTITY_SERVICE_CREDENTIAL": SERVICE_CREDENTIAL,
        "IDENTITY_SERVICE_CREDENTIAL": SERVICE_CREDENTIAL,
        # Concurrent `uv run` calls sync the same environment; this stack starts
        # thirteen of them at once.
        "UV_NO_SYNC": "1",
        **service.extra,
    }
    env.pop("VIRTUAL_ENV", None)
    return env


def wait_for(check, *, what: str, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.5)
    msg = f"{what} did not become ready within {timeout}s"
    raise TimeoutError(msg)


def http_ok(url: str, *, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


# ------------------------------------------------------------------ infra


def start_infrastructure() -> None:
    if run(["docker", "inspect", POSTGRES_CONTAINER]).returncode != 0:
        print(f"  starting PostgreSQL on {POSTGRES_PORT} …")
        result = run(
            [
                "docker", "run", "-d", "--rm",
                "--name", POSTGRES_CONTAINER,
                "-e", f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
                "-p", f"127.0.0.1:{POSTGRES_PORT}:5432",
                # tmpfs: the whole point is that `down` leaves nothing behind.
                "--tmpfs", "/var/lib/postgresql/data",
                POSTGRES_IMAGE,
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
    else:
        print("  PostgreSQL already running")

    if run(["docker", "inspect", NATS_CONTAINER]).returncode != 0:
        print(f"  starting NATS on {NATS_PORT} …")
        result = run(
            [
                "docker", "run", "-d", "--rm",
                "--name", NATS_CONTAINER,
                "-p", f"127.0.0.1:{NATS_PORT}:4222",
                NATS_IMAGE, "-js",
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
    else:
        print("  NATS already running")

    wait_for(
        lambda: run(
            ["docker", "exec", POSTGRES_CONTAINER, "pg_isready", "-U", "postgres"]
        ).returncode
        == 0,
        what="PostgreSQL",
    )


def create_databases() -> None:
    for service in SERVICES:
        name = f"{service.name}_db"
        exists = run(
            [
                "docker", "exec", POSTGRES_CONTAINER,
                "psql", "-U", "postgres", "-tAc",
                f"SELECT 1 FROM pg_database WHERE datname='{name}'",
            ]
        )
        if exists.stdout.strip() == "1":
            continue
        created = run(
            [
                "docker", "exec", POSTGRES_CONTAINER,
                "psql", "-U", "postgres", "-c", f'CREATE DATABASE "{name}"',
            ]
        )
        if created.returncode != 0:
            raise RuntimeError(f"{name}: {created.stderr.strip()}")
    print(f"  {len(SERVICES)} databases ready — one per service (ADR-0011)")


def migrate(service: Service) -> tuple[bool, str]:
    directory = REPO_ROOT / "services" / service.name
    result = run(
        ["uv", "run", "alembic", "-c", str(directory / "alembic.ini"), "upgrade", "head"],
        cwd=directory,
        env=service_env(service),
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()[-600:]
    return True, "head"


# ------------------------------------------------------------------ services


def start_service(service: Service) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = (LOG_DIR / f"{service.name}.log").open("wb")
    process = subprocess.Popen(  # noqa: S603
        [
            "uv", "run", "uvicorn", f"{service.name}.main:app",
            "--host", "127.0.0.1", "--port", str(service.port),
            "--log-level", "info",
        ],
        cwd=REPO_ROOT / "services" / service.name,
        env=service_env(service),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return process.pid


def read_state() -> dict:
    if not STATE_FILE.is_file():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def write_state(state: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


# ------------------------------------------------------------------ commands


def command_up(args: argparse.Namespace) -> int:
    if not docker_available():
        print("Docker is not available. It is needed for PostgreSQL and NATS.")
        return 1

    print("Infrastructure")
    start_infrastructure()
    create_databases()

    print("\nMigrations")
    failed = []
    for service in SERVICES:
        ok, detail = migrate(service)
        print(f"  {'ok  ' if ok else 'FAIL'} {service.name:<14} {detail if not ok else ''}")
        if not ok:
            failed.append(service.name)
    if failed:
        print(f"\nMigrations failed: {', '.join(failed)}")
        return 1

    print("\nServices")
    state = read_state()
    for service in SERVICES:
        existing = state.get(service.name)
        if existing and alive(existing["pid"]) and http_ok(
            f"http://127.0.0.1:{service.port}/health"
        ):
            print(f"  ok   {service.name:<14} already running on {service.port}")
            continue
        pid = start_service(service)
        state[service.name] = {"pid": pid, "port": service.port}
        write_state(state)

    for service in SERVICES:
        url = f"http://127.0.0.1:{service.port}/health"
        try:
            wait_for(lambda u=url: http_ok(u), what=service.name, timeout=args.timeout)
            print(f"  ok   {service.name:<14} http://127.0.0.1:{service.port}")
        except TimeoutError:
            log = LOG_DIR / f"{service.name}.log"
            tail = log.read_text(errors="replace")[-800:] if log.is_file() else ""
            print(f"  FAIL {service.name:<14} did not answer /health\n{tail}")
            failed.append(service.name)

    print("\nOpenAPI")
    for service in SERVICES:
        print(f"  http://127.0.0.1:{service.port}/docs   {service.name}")

    if failed:
        print(f"\n{len(failed)} service(s) failed to start: {', '.join(failed)}")
        return 1
    print(f"\nAll {len(SERVICES)} services are up.")
    print("Exercise them with: uv run python scripts/dev/journeys.py")
    return 0


def command_status(_: argparse.Namespace) -> int:
    state = read_state()
    print(f"{'service':<14} {'port':>5}  {'pid':>7}  health  ready")
    for service in SERVICES:
        entry = state.get(service.name, {})
        pid = entry.get("pid", 0)
        running = alive(pid) if pid else False
        health = http_ok(f"http://127.0.0.1:{service.port}/health")
        ready = http_ok(f"http://127.0.0.1:{service.port}/ready")
        print(
            f"{service.name:<14} {service.port:>5}  {pid:>7}  "
            f"{'up' if health else '--':<6}  {'yes' if ready else 'no':<5}"
            f"{'' if running else '   (process gone)'}"
        )
    return 0


def command_logs(args: argparse.Namespace) -> int:
    log = LOG_DIR / f"{args.service}.log"
    if not log.is_file():
        print(f"no log for {args.service}")
        return 1
    print(log.read_text(errors="replace")[-args.tail_bytes :])
    return 0


def command_down(_: argparse.Namespace) -> int:
    state = read_state()
    for name, entry in state.items():
        pid = entry.get("pid")
        if pid and alive(pid):
            # Already gone is the outcome we wanted anyway.
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"  stopped {name}")
    if STATE_FILE.is_file():
        STATE_FILE.unlink()
    for container in (POSTGRES_CONTAINER, NATS_CONTAINER):
        if run(["docker", "inspect", container]).returncode == 0:
            run(["docker", "rm", "-f", container])
            print(f"  removed {container}")
    print("\nStopped. The database was in a tmpfs, so nothing was left on disk.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the whole platform")
    up.add_argument("--timeout", type=int, default=60)
    up.set_defaults(func=command_up)

    sub.add_parser("status", help="what is running").set_defaults(func=command_status)

    logs = sub.add_parser("logs", help="tail one service's log")
    logs.add_argument("service", choices=[s.name for s in SERVICES])
    logs.add_argument("--tail-bytes", type=int, default=4000)
    logs.set_defaults(func=command_logs)

    sub.add_parser("down", help="stop everything").set_defaults(func=command_down)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
