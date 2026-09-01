"""Docker Compose, generated material, and NATS helpers for security proof."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .constants import (
    ALLOWED_HOSTS,
    COMPOSE_PROFILE,
    COMPOSE_PROJECT,
    FETCH_TIMEOUT_SECONDS,
    FORBIDDEN_URL_FRAGMENTS,
    INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    NATS_CONTAINER,
    NATS_SERVICE,
    NETWORK_NAME,
    SECURITY_INIT_IMAGE,
    VOLUME_GENERATED_NAME,
    VOLUME_JS_NAME,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = REPO_ROOT / "infra" / "labs" / "nats-security-proof"
COMPOSE_FILE = LAB_ROOT / "compose.yaml"
MANIFEST_FILE = LAB_ROOT / "identity-manifest.yaml"
BRIDGE_SERVICE = REPO_ROOT / "services" / "legacy_event_bridge"
AUDIT_SERVICE = REPO_ROOT / "services" / "audit"
PROBES_DIR = Path(__file__).resolve().parent / "probes"


class _PortCache:
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


def build_tls_nats_url(*, port: int, host: str = "127.0.0.1") -> str:
    url = f"tls://{host}:{port}"
    assert_lab_nats_url(url)
    return url


def discover_host_port(service: str, container_port: int, *, force_refresh: bool = False) -> int:
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
    if service == NATS_SERVICE:
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


def _wait_for_nats_healthy(*, timeout_seconds: float = 45.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        inspect = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                "hudhud-nats-security-proof-nats",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        status = inspect.stdout.strip()
        if status == "healthy":
            return
        if status in {"exited", "dead"}:
            logs = subprocess.run(
                ["docker", "logs", "hudhud-nats-security-proof-nats"],
                capture_output=True,
                text=True,
                check=False,
            )
            msg = logs.stderr.strip() or logs.stdout.strip() or "nats container exited"
            raise AssertionError(msg)
        time.sleep(1)
    msg = "nats container did not become healthy in time"
    raise AssertionError(msg)


def _run_topology_bootstrap() -> None:
    result = compose("run", "--rm", "topology-bootstrap")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HUDHUD_NATS_SECURITY_TOPOLOGY_BOOTSTRAPPED" in result.stdout


def extract_generated_material() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="hudhud-nats-security-proof-"))
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{VOLUME_GENERATED_NAME}:/generated:ro",
            "-v",
            f"{temp_dir}:/out",
            "alpine:3.20",
            "sh",
            "-c",
            "cp -a /generated/. /out/",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"failed to extract generated material: {msg}")
    return temp_dir


def compose_up() -> tuple[int, Path]:
    _port_cache.nats = None
    down = compose("down", "-v", "--remove-orphans")
    assert down.returncode == 0, down.stderr
    build = compose("build", "security-init")
    assert build.returncode == 0, build.stdout + build.stderr
    up = compose("up", "-d", NATS_SERVICE)
    assert up.returncode == 0, up.stdout + up.stderr
    _wait_for_nats_healthy()
    _run_topology_bootstrap()
    nats_port = discover_host_port(NATS_SERVICE, 4222)
    generated_dir = extract_generated_material()
    return nats_port, generated_dir


def compose_down() -> None:
    result = compose("down", "-v", "--remove-orphans")
    assert result.returncode == 0, result.stderr
    _port_cache.nats = None


def dedicated_resources_absent() -> bool:
    checks = [
        subprocess.run(
            ["docker", "network", "inspect", NETWORK_NAME],
            capture_output=True,
            check=False,
        ),
        subprocess.run(
            ["docker", "volume", "inspect", VOLUME_JS_NAME],
            capture_output=True,
            check=False,
        ),
        subprocess.run(
            ["docker", "volume", "inspect", VOLUME_GENERATED_NAME],
            capture_output=True,
            check=False,
        ),
    ]
    return all(item.returncode != 0 for item in checks)


def creds_path(generated_dir: Path, identity: str) -> Path:
    return generated_dir / "creds" / f"{identity}.creds"


def ca_path(generated_dir: Path) -> Path:
    return generated_dir / "ca" / "ca.pem"


def revoke_user(identity: str) -> None:
    """Revoke a NATS user JWT via nsc and refresh resolver account JWT."""
    script_lines = [
        "set -eu",
        "export NSC_HOME=/generated/nsc-home",
        "export NKEYS_PATH=/generated/nsc-home/keys",
        "nsc env -s /generated/nsc-home",
        (
            f"if nsc describe user --account HUDHUD --name {identity} >/dev/null 2>&1; then"
        ),
        f"  nsc delete user --account HUDHUD --name {identity} --revoke",
        "fi",
        (
            "ACCOUNT_ID=$(nsc describe account HUDHUD | awk -F'|' "
            "'/Account ID/ {gsub(/^[ \\t]+|[ \\t]+$/, \"\", $3); print $3}')"
        ),
        "ACCOUNT_JWT_PATH=/generated/nsc-home/HUDHUD/accounts/HUDHUD/HUDHUD.jwt",
        'cp "$ACCOUNT_JWT_PATH" "/generated/jwt/accounts/${ACCOUNT_ID}.jwt"',
        f'echo "revoked={identity}"',
    ]
    script = "\n".join(script_lines)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{VOLUME_GENERATED_NAME}:/generated",
            "-e",
            "NSC_HOME=/generated/nsc-home",
            "-e",
            "NKEYS_PATH=/generated/nsc-home/keys",
            SECURITY_INIT_IMAGE,
            "/bin/sh",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"failed to revoke user {identity}: {msg}")
    reload_nats_jwt_resolver()


def reload_nats_jwt_resolver() -> None:
    """Restart NATS so updated account JWT claims are loaded by the resolver."""
    result = subprocess.run(
        ["docker", "restart", NATS_CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"failed to restart NATS after JWT update: {msg}")
    _port_cache.nats = None
    _wait_for_nats_healthy()
    discover_host_port(NATS_SERVICE, 4222, force_refresh=True)


def wait_for_resolver_propagation(*, seconds: float = 2.0) -> None:
    """Bounded pause after NATS JWT resolver reload."""
    time.sleep(seconds)


def _probe_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.update(extra)
    return env


def run_bridge_publish_probe(
    *,
    nats_url: str,
    ca_file: Path,
    creds_file: Path,
    subject: str,
    payload: str,
    msg_id: str,
) -> dict[str, object]:
    try:
        result = subprocess.run(
            ["uv", "run", "--with", "nkeys", "python", str(PROBES_DIR / "bridge_publish_tls.py")],
            cwd=BRIDGE_SERVICE,
            env=_probe_env(
                {
                    "NATS_URL": nats_url,
                    "NATS_TLS_CA_FILE": str(ca_file),
                    "NATS_CREDS_FILE": str(creds_file),
                    "PUBLISH_SUBJECT": subject,
                    "PUBLISH_PAYLOAD": payload,
                    "PUBLISH_MSG_ID": msg_id,
                }
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=INTEGRATION_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"puback_received": False, "error_type": "TimeoutExpired"}
    if result.returncode != 0 and not result.stdout.strip():
        msg = result.stderr.strip() or "bridge publish probe failed"
        raise RuntimeError(msg)
    return json.loads(result.stdout.strip())


def run_audit_bind_pull_probe(
    *,
    nats_url: str,
    ca_file: Path,
    creds_file: Path,
) -> dict[str, object]:
    result = subprocess.run(
        ["uv", "run", "--with", "nkeys", "python", str(PROBES_DIR / "audit_bind_pull_ack.py")],
        cwd=AUDIT_SERVICE,
        env=_probe_env(
            {
                "NATS_URL": nats_url,
                "NATS_TLS_CA_FILE": str(ca_file),
                "NATS_CREDS_FILE": str(creds_file),
                "FETCH_TIMEOUT_SECONDS": str(FETCH_TIMEOUT_SECONDS),
            }
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=INTEGRATION_OPERATION_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 and not result.stdout.strip():
        msg = result.stderr.strip() or "audit bind/pull probe failed"
        raise RuntimeError(msg)
    return json.loads(result.stdout.strip())


def run_audit_readiness_probe(
    *,
    nats_url: str,
    ca_file: Path,
    creds_file: Path,
) -> dict[str, object]:
    try:
        result = subprocess.run(
            ["uv", "run", "--with", "nkeys", "python", str(PROBES_DIR / "audit_readiness_tls.py")],
            cwd=AUDIT_SERVICE,
            env=_probe_env(
                {
                    "NATS_URL": nats_url,
                    "NATS_TLS_CA_FILE": str(ca_file),
                    "NATS_CREDS_FILE": str(creds_file),
                }
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=INTEGRATION_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"binding_verified": False, "error_type": "TimeoutExpired"}
    if not result.stdout.strip():
        msg = result.stderr.strip() or "audit readiness probe failed"
        raise RuntimeError(msg)
    return json.loads(result.stdout.strip())
