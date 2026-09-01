"""Fixtures for the observation eventing proof lab."""

from __future__ import annotations

import pytest

from .helpers import (
    audit_service_url,
    bridge_service_url,
    build_nats_url,
    compose_down,
    compose_up,
    dedicated_resources_absent,
    docker_available,
    prepare_service_databases,
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    cleanup_items = [item for item in items if "test_cleanup_integration" in item.nodeid]
    other_items = [item for item in items if item not in cleanup_items]
    items[:] = other_items + cleanup_items


@pytest.fixture(scope="session")
def eventing_proof_stack():
    if not docker_available():
        pytest.skip("Docker runtime not available")
    postgres_port, nats_port = compose_up()
    prepare_service_databases(postgres_port)
    stack = {
        "postgres_port": postgres_port,
        "nats_port": nats_port,
        "bridge_database_url": bridge_service_url(postgres_port),
        "audit_database_url": audit_service_url(postgres_port),
        "nats_url": build_nats_url(port=nats_port),
    }
    yield stack
    if not dedicated_resources_absent():
        compose_down()
    assert dedicated_resources_absent(), (
        "observation eventing proof resources still present after teardown"
    )
