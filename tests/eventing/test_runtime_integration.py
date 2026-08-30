"""Docker runtime validation for eventing foundation (skipped when Docker unavailable)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from .conftest import COMPOSE_FILE, REPO_ROOT

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
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
        ["docker", "compose", "-f", str(COMPOSE_FILE), "--profile", "eventing", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_bootstrap() -> subprocess.CompletedProcess[str]:
    return _compose("run", "--rm", "eventing-bootstrap")


def _run_smoke() -> subprocess.CompletedProcess[str]:
    return _compose(
        "run",
        "--rm",
        "eventing-bootstrap",
        "uv",
        "run",
        "python",
        "infra/eventing/scripts/smoke_publish.py",
    )


def _fetch_jsz() -> dict:
    result = _compose(
        "exec",
        "-T",
        "nats",
        "wget",
        "-qO-",
        "http://127.0.0.1:8222/jsz?streams=1",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _stream_names(jsz: dict) -> set[str]:
    details = jsz.get("account_details", [{}])[0].get("stream_detail", [])
    return {item["name"] for item in details}


@pytest.fixture(scope="module")
def eventing_stack():
    if not _docker_available():
        pytest.skip("Docker runtime not available")

    up = _compose("up", "-d", "nats")
    assert up.returncode == 0, up.stderr

    bootstrap = _run_bootstrap()
    assert bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr
    assert "HUDHUD_EVENTING_TOPOLOGY_BOOTSTRAPPED" in bootstrap.stdout

    yield

    down = _compose("down", "-v")
    assert down.returncode == 0, down.stderr


def test_bootstrap_is_idempotent(eventing_stack) -> None:
    first = _run_bootstrap()
    assert first.returncode == 0, first.stdout + first.stderr
    assert first.stdout.count("created:") == 0
    assert "exists:" in first.stdout


def test_nats_health_and_jetstream_metrics(eventing_stack) -> None:
    health = _compose(
        "exec",
        "-T",
        "nats",
        "wget",
        "-qO-",
        "http://127.0.0.1:8222/healthz",
    )
    assert health.returncode == 0, health.stderr
    assert health.stdout.strip() in {"ok", '{"status":"ok"}'}

    jsz = _fetch_jsz()
    assert "HUDHUD_SHIPMENT" in _stream_names(jsz)


def test_publish_smoke_on_internal_network(eventing_stack) -> None:
    smoke = _run_smoke()
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert "HUDHUD_EVENTING_SMOKE_OK" in smoke.stdout


def test_restart_preserves_jetstream_file_storage(eventing_stack) -> None:
    before = _run_smoke()
    assert before.returncode == 0, before.stdout + before.stderr

    restart = _compose("restart", "nats")
    assert restart.returncode == 0, restart.stderr

    health = _compose(
        "exec",
        "-T",
        "nats",
        "wget",
        "-qO-",
        "http://127.0.0.1:8222/healthz",
    )
    assert health.returncode == 0, health.stderr

    jsz = _fetch_jsz()
    assert "HUDHUD_PICKUP" in _stream_names(jsz)

    bootstrap = _run_bootstrap()
    assert bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr
    assert bootstrap.stdout.count("created:") == 0
