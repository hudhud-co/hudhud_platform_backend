"""Process control for the real Identity service used by the integration proof."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_DIR = REPO_ROOT / "services" / "identity"
PICKUP_DIR = REPO_ROOT / "services" / "pickup"

SIGNING_KEY = "integration-identity-signing-key-32"
SERVICE_CREDENTIAL = "integration-pickup-credential-32ch"  # noqa: S105 - local fixture
BOOTSTRAP_OPERATIONS_PHONE = "+9647701820900"
_CODE_LINE = re.compile(r"reference=(\S+) phone=\S+ code=(\d+)")


@dataclass(frozen=True, slots=True)
class IdentityProcess:
    base_url: str
    process: subprocess.Popen
    log_path: Path

    def code_for(self, reference: str, *, timeout: float = 10.0) -> str:
        """Read the dev-channel code the service printed for one challenge."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                match = _CODE_LINE.search(line)
                if match and match.group(1) == reference:
                    return match.group(2)
            time.sleep(0.05)
        msg = f"no OTP code was emitted for challenge {reference}"
        raise AssertionError(msg)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            msg = f"identity exited early:\n{log_path.read_text(encoding='utf-8')}"
            raise RuntimeError(msg)
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.2)
    msg = f"identity did not become healthy:\n{log_path.read_text(encoding='utf-8')}"
    raise RuntimeError(msg)


def run_in_pickup(script: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """Execute a snippet inside the Pickup service's own environment.

    Running it there — rather than importing pickup here — is what keeps this an
    integration proof rather than a cross-service import.
    """
    env = dict(os.environ)
    env["VIRTUAL_ENV"] = ""
    # See the note in the proof labs' env builders: concurrent `uv run` syncs race on a
    # shared virtualenv and can half-write a native library.
    env["UV_NO_SYNC"] = "1"
    env.update(env_extra)
    return subprocess.run(  # noqa: S603
        ["uv", "run", "python", "-c", script],
        cwd=PICKUP_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def start_identity(log_path: Path) -> IdentityProcess:
    """Launch Identity on a free port and wait until it answers /health."""
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "IDENTITY_ENVIRONMENT": "local",
            "IDENTITY_SIGNING_KEY": SIGNING_KEY,
            "IDENTITY_SERVICE_CREDENTIALS": f"pickup:{SERVICE_CREDENTIAL}",
            "IDENTITY_OTP_DELIVERY_CHANNEL": "console",
            # A fresh deployment has no operator; exactly one configured phone becomes
            # the first one, which is how a real environment bootstraps too.
            "IDENTITY_BOOTSTRAP_OPERATIONS_PHONE": BOOTSTRAP_OPERATIONS_PHONE,
            # No DATABASE_URL: the service falls back to its in-memory unit of work. The
            # subject of this proof is authorization, not storage.
            "IDENTITY_DATABASE_URL": "",
            "DATABASE_URL": "",
            "VIRTUAL_ENV": "",
        }
    )
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(  # noqa: S603
            [
                "uv", "run", "python", "-m", "uvicorn", "identity.main:app",
                "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
            ],
            cwd=IDENTITY_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_health(base_url, process, log_path)
    return IdentityProcess(base_url=base_url, process=process, log_path=log_path)


__all__ = [
    "BOOTSTRAP_OPERATIONS_PHONE",
    "IdentityProcess",
    "SERVICE_CREDENTIAL",
    "SIGNING_KEY",
    "run_in_pickup",
    "start_identity",
]
