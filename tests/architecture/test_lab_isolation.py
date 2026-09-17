"""Guards that keep the Docker proof labs from standing on each other.

Two classes of collision cost a long time to diagnose, because both surface far from
their cause and only when suites share a session:

* **shared names.** Every lab used to hard-code its Compose project, network, volumes and
  container names. Two labs, or two sessions, then tore down each other's containers
  mid-run and the failures looked like flaky eventing.
* **a container writing into the host's virtualenv.** The labs bind-mount the repository
  read-write. A container that runs `uv` without redirecting `UV_PROJECT_ENVIRONMENT`
  syncs into the host's `./.venv` and replaces macOS wheels with Linux ones. That
  surfaces as `dlopen(...): slice is not valid mach-o file` in whichever unrelated suite
  imports the library next.

Both are now prevented rather than remembered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LABS = REPO_ROOT / "infra" / "labs"
TESTS_ROOT = REPO_ROOT / "tests"
SUFFIX_TOKEN = "${HUDHUD_LAB_SUFFIX:-}"

#: The labs that start a Compose project a test session drives.
NAMESPACED_LABS = (
    "nats-security-proof",
    "observation-eventing-proof",
    "pickup-acceptance-eventing-proof",
    "service-postgres-proof",
)


def compose_files() -> list[Path]:
    """Every compose file in the repository, not only the labs'.

    The one that caused this was `infra/compose/eventing-foundation.compose.yaml`, which
    is not under `infra/labs/` at all.
    """
    return sorted(
        {
            *(REPO_ROOT / "infra").rglob("compose.yaml"),
            *(REPO_ROOT / "infra").rglob("*.compose.yaml"),
        }
    )


@pytest.mark.parametrize("lab", NAMESPACED_LABS)
def test_every_named_resource_carries_the_session_suffix(lab: str) -> None:
    """`name:` and `container_name:` must be per-session, or two labs collide."""
    text = (LABS / lab / "compose.yaml").read_text(encoding="utf-8")
    unsuffixed = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*(name|container_name):\s*hudhud[-_]", line)
        and SUFFIX_TOKEN not in line
    ]
    assert unsuffixed == [], unsuffixed


@pytest.mark.parametrize("lab", NAMESPACED_LABS)
def test_the_lab_declares_its_project_network_and_volumes(lab: str) -> None:
    """A lab with no dedicated network or volume would share the default ones."""
    rendered = yaml.safe_load((LABS / lab / "compose.yaml").read_text(encoding="utf-8"))
    assert rendered.get("name", "").startswith("hudhud-")
    assert rendered.get("networks"), lab
    assert rendered.get("volumes"), lab


def test_no_container_syncs_into_the_hosts_virtualenv() -> None:
    """A bind-mounted repo plus a container `uv run` overwrites the host's `.venv`."""
    offenders = []
    for path in compose_files():
        rendered = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, service in (rendered.get("services") or {}).items():
            command = " ".join(
                service.get("command") or []
                if isinstance(service.get("command"), list)
                else [str(service.get("command") or "")]
            )
            if "uv run" not in command and "uv sync" not in command:
                continue
            mounts = service.get("volumes") or []
            mounts_repo = any(
                isinstance(m, dict)
                and str(m.get("source", "")).startswith("..")
                and str(m.get("target", "")) in {"/repo", "/workspace", "/app"}
                for m in mounts
            )
            if not mounts_repo:
                continue
            environment = service.get("environment") or {}
            if not environment.get("UV_PROJECT_ENVIRONMENT"):
                offenders.append(f"{path.parent.name}/{name}")
    assert offenders == [], offenders


def test_every_lab_cleanup_script_is_suffix_aware() -> None:
    """A cleanup that targets the old fixed names would leave this run's mess behind."""
    offenders = []
    for lab in NAMESPACED_LABS:
        script = LABS / lab / "scripts" / "cleanup.sh"
        if not script.is_file():
            continue
        text = script.read_text(encoding="utf-8")
        if "HUDHUD_LAB_SUFFIX" not in text:
            offenders.append(lab)
    assert offenders == []


def test_no_lab_helper_hardcodes_a_container_name() -> None:
    """A hard-coded name inspects another lab's container instead of this one's."""
    offenders = []
    for lab in NAMESPACED_LABS:
        suite = REPO_ROOT / "tests" / lab.replace("-", "_")
        for path in suite.glob("*.py"):
            if path.name in {"constants.py", "test_compose_topology.py"}:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if re.search(r'"hudhud-[a-z-]*proof-[a-z-]+"', line):
                    offenders.append(f"{path.name}:{number}")
    assert offenders == []


def test_the_migration_proof_labs_randomise_their_own_names() -> None:
    """These never had the problem, and the guard keeps it that way."""
    helpers = (
        REPO_ROOT / "tests" / "new_service_migration_proof" / "helpers.py"
    ).read_text(encoding="utf-8")
    assert "secrets.token_hex" in helpers
    assert "--rm" in helpers


def test_lab_subprocesses_do_not_resync_the_shared_environment() -> None:
    """Concurrent `uv run` syncs race and can half-write a native library."""
    missing = []
    for suite in (
        "nats_security_proof",
        "observation_eventing_proof",
        "pickup_acceptance_eventing_proof",
        "service_postgres_proof",
        "new_service_migration_proof",
        "identity_pickup_integration",
    ):
        directory = REPO_ROOT / "tests" / suite
        text = "\n".join(
            path.read_text(encoding="utf-8") for path in directory.glob("*.py")
        )
        if '"uv"' in text and "UV_NO_SYNC" not in text:
            missing.append(suite)
    assert missing == []


# ------------------------------------------------- reading a published port


LAB_HELPERS = (
    "nats_security_proof",
    "observation_eventing_proof",
    "pickup_acceptance_eventing_proof",
    "service_postgres_proof",
)


def test_no_lab_reads_a_published_port_once() -> None:
    """`docker compose port` answers before a restarted container has published one.

    All four labs had the same line — take the last line of the output and split it on a
    colon — and all four would turn a mid-session restart into
    `published on unexpected host: ''`. Only the NATS security proof actually restarts
    anything, which is why it appeared exactly once, in a full-suite run, and looked like
    a security assertion rather than a race.

    They now share `tests/lab_ports.published_binding`, which polls.
    """
    offenders = []
    for lab in LAB_HELPERS:
        source = (TESTS_ROOT / lab / "helpers.py").read_text(encoding="utf-8")
        if "splitlines()[-1]" in source:
            offenders.append(lab)
    assert offenders == [], (
        f"{offenders} read a published port from one unpolled command; "
        "use tests/lab_ports.published_binding"
    )


def test_every_lab_discovers_its_port_through_the_shared_helper() -> None:
    for lab in LAB_HELPERS:
        source = (TESTS_ROOT / lab / "helpers.py").read_text(encoding="utf-8")
        assert "from lab_ports import published_binding" in source, lab


def test_the_loopback_check_is_still_a_hard_failure() -> None:
    """Polling must not turn a service published on 0.0.0.0 into something to wait out."""
    source = (TESTS_ROOT / "lab_ports.py").read_text(encoding="utf-8")
    assert "published on unexpected host" in source
    assert "raise UnexpectedPublishHost" in source
