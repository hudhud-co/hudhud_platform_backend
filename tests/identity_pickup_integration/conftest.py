"""Session fixture that boots the real Identity service for the integration proof."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest

from identity_pickup_integration.harness import IdentityProcess, start_identity


@pytest.fixture(scope="session")
def identity_service(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[IdentityProcess]:
    log_path = tmp_path_factory.mktemp("identity") / "identity.log"
    service = start_identity(log_path)
    try:
        yield service
    finally:
        service.process.terminate()
        try:
            service.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            service.process.kill()
