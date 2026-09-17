"""One namespace per lab, per session, so the Docker proof suites stop colliding.

Four suites — `nats_security_proof`, `observation_eventing_proof`,
`pickup_acceptance_eventing_proof` and `service_postgres_proof` — each start a Compose
project with a **fixed** name, a fixed network, fixed volumes and fixed container names.
Run alone, every one of them passes. Run two in the same pytest session, or two sessions
at once, and they tear down each other's containers halfway through: the failures look
like flaky eventing and are actually two labs sharing a network.

This module gives each lab a suffix, exported as ``HUDHUD_LAB_SUFFIX`` so that Docker
Compose interpolates it into `name:`, `container_name:`, the network and the volumes. The
prefixes are unchanged, so a stranded resource is still findable with
``docker compose ls | grep hudhud-`` and the sweeper below still finds it.

Pin the suffix with ``HUDHUD_LAB_SUFFIX`` when debugging — two runs with the same value
share a lab on purpose, which is what you want when inspecting one that failed.
"""

from __future__ import annotations

import os
import secrets
import subprocess

#: Set this to reuse a lab across runs. Unset means a fresh namespace per process.
SUFFIX_ENV = "HUDHUD_LAB_SUFFIX"

#: Every lab resource this repository creates starts with one of these.
LAB_PREFIXES = (
    "hudhud-nats-security-proof",
    "hudhud-observation-eventing-proof",
    "hudhud-pickup-acceptance-eventing-proof",
    "hudhud-service-postgres-proof",
    "hudhud-migration-proof",
)


def _resolve_suffix() -> str:
    pinned = os.environ.get(SUFFIX_ENV)
    if pinned is not None:
        # Including the empty string: an explicit "" restores the old fixed names, which
        # is what a CI job wants when it runs one suite per job and cleans up by name.
        return pinned
    generated = f"-{secrets.token_hex(4)}"
    # Exported rather than returned, because Docker Compose reads it from the
    # environment when it interpolates the compose file.
    os.environ[SUFFIX_ENV] = generated
    return generated


#: Computed once per process. Every constants module reads this, so a single lab's
#: resources all agree with each other and disagree with every other lab's.
LAB_SUFFIX = _resolve_suffix()


def namespaced(name: str) -> str:
    """Append this process's suffix to a fixed lab resource name."""
    return f"{name}{LAB_SUFFIX}"


def base(name: str) -> str:
    """Strip this process's suffix back off.

    Tests that compare a constant against the literal text of a compose file or a
    cleanup script need the unsuffixed name, because those files carry
    ``${HUDHUD_LAB_SUFFIX:-}`` rather than any one run's value.
    """
    if LAB_SUFFIX and name.endswith(LAB_SUFFIX):
        return name[: -len(LAB_SUFFIX)]
    return name


def stranded_lab_resources() -> dict[str, list[str]]:
    """Every lab container, network and volume left behind by any run.

    Used by the sweeper test so a crashed lab is reported rather than quietly leaking a
    volume that the next run then finds populated.
    """
    found: dict[str, list[str]] = {"containers": [], "networks": [], "volumes": []}
    probes = (
        ("containers", ["docker", "ps", "-a", "--format", "{{.Names}}"]),
        ("networks", ["docker", "network", "ls", "--format", "{{.Name}}"]),
        ("volumes", ["docker", "volume", "ls", "--format", "{{.Name}}"]),
    )
    for kind, command in probes:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=30
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            name = line.strip()
            if any(
                name.startswith(prefix) or name.startswith(prefix.replace("-", "_"))
                for prefix in LAB_PREFIXES
            ):
                found[kind].append(name)
    return found
