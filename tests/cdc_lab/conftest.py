"""Fixtures for the isolated legacy CDC lab."""

from __future__ import annotations

import pytest

from .helpers import CdcLabClient, compose, docker_available, unique_slot


@pytest.fixture(scope="module")
def cdc_lab_stack():
    if not docker_available():
        pytest.skip("Docker runtime not available")

    down = compose("down", "-v", "--remove-orphans")
    assert down.returncode == 0, down.stderr

    up = compose("up", "-d", "--wait", "postgres")
    assert up.returncode == 0, up.stdout + up.stderr

    client = CdcLabClient()
    client.psql("SELECT 1;")

    yield client

    teardown = compose("down", "-v", "--remove-orphans")
    assert teardown.returncode == 0, teardown.stderr


@pytest.fixture
def cdc_client(cdc_lab_stack: CdcLabClient) -> CdcLabClient:
    cdc_lab_stack.reset_probe_table()
    return cdc_lab_stack


@pytest.fixture
def lab_slot(cdc_client: CdcLabClient) -> str:
    slot = unique_slot()
    cdc_client.create_slot(slot)
    yield slot
    if cdc_client.slot_exists(slot):
        cdc_client.drop_slot(slot)
