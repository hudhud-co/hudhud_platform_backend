"""Reading a lab container's published host port, without racing its restart.

Every proof lab asks Docker the same question — "which host port is this service
published on?" — and each had the same fragile answer:

    binding = result.stdout.strip().splitlines()[-1].strip()
    host, _, port_text = binding.rpartition(":")

`docker compose port` succeeds while a container that has just been restarted still has
no published port. It prints a blank line, or a line with no `host:port` in it, and that
last line then parses to an empty host. The NATS security proof restarts NATS to reload
the JWT resolver, so the failure appeared only in a full-suite run and looked like this:

    AssertionError: nats published on unexpected host: ''

which says nothing about a restart and points at the security check instead of the race.

So: poll until Docker reports a real binding. The loopback check is unchanged and still
fails loudly — a service genuinely published on `0.0.0.0` is a finding, not a race, and
is raised immediately rather than waited out.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

#: Every lab publishes to loopback only. A binding anywhere else is a real finding.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


class UnexpectedPublishHost(AssertionError):
    """The service is published somewhere other than loopback."""


def published_binding(
    compose: Callable[..., subprocess.CompletedProcess[str]],
    service: str,
    container_port: int,
    *,
    allowed_hosts: frozenset[str] = LOOPBACK_HOSTS,
    timeout_seconds: float = 30.0,
) -> tuple[str, int]:
    """Poll ``docker compose port`` until it reports a real published binding.

    ``compose`` is the lab's own compose runner, so each lab keeps its own project name,
    file and environment; only the reading of the answer is shared.
    """
    deadline = time.time() + timeout_seconds
    last_seen = ""
    while True:
        result = compose("port", service, str(container_port))
        if result.returncode == 0:
            for line in reversed(result.stdout.strip().splitlines()):
                host, separator, port_text = line.strip().rpartition(":")
                if not separator or not port_text.isdigit() or int(port_text) == 0:
                    # Not a binding yet — Docker has answered before publishing.
                    continue
                if host not in allowed_hosts:
                    msg = f"{service} published on unexpected host: {host!r}"
                    raise UnexpectedPublishHost(msg)
                return host, int(port_text)
            last_seen = result.stdout.strip()
        else:
            last_seen = result.stderr.strip() or result.stdout.strip()
        if time.time() >= deadline:
            break
        time.sleep(0.25)
    msg = (
        f"{service} published no host port for container port {container_port} "
        f"within {timeout_seconds:.0f}s; last output: {last_seen!r}"
    )
    raise RuntimeError(msg)
