"""Fixtures for the NATS security proof lab."""

from __future__ import annotations

import shutil

import pytest

from .helpers import (
    compose_down,
    compose_up,
    dedicated_resources_absent,
    docker_available,
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    cleanup_items = [item for item in items if "test_cleanup_integration" in item.nodeid]
    other_items = [item for item in items if item not in cleanup_items]
    items[:] = other_items + cleanup_items


@pytest.fixture(scope="session")
def nats_security_stack():
    if not docker_available():
        pytest.skip("Docker runtime not available")
    nats_port, generated_dir = compose_up()
    stack = {
        "nats_port": nats_port,
        "generated_dir": generated_dir,
    }
    yield stack
    shutil.rmtree(generated_dir, ignore_errors=True)
    if not dedicated_resources_absent():
        compose_down()
    assert dedicated_resources_absent(), (
        "nats security proof resources still present after teardown"
    )
