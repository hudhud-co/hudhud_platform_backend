"""Fixtures for the service PostgreSQL proof lab."""

from __future__ import annotations

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
def postgres_proof_stack():
    if not docker_available():
        pytest.skip("Docker runtime not available")
    port = compose_up()
    yield port
    if not dedicated_resources_absent():
        compose_down()
    assert dedicated_resources_absent(), (
        "service postgres proof resources still present after teardown"
    )
