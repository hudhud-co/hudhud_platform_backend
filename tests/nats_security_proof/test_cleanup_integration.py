"""Dedicated cleanup verification for the NATS security proof lab."""

from __future__ import annotations

import pytest

from .helpers import compose_down, dedicated_resources_absent, docker_available

pytestmark = pytest.mark.integration


def test_cleanup_removes_dedicated_resources() -> None:
    if not docker_available():
        pytest.skip("Docker runtime not available")
    if not dedicated_resources_absent():
        compose_down()
    assert dedicated_resources_absent()
