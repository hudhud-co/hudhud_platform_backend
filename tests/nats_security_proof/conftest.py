"""Fixtures for the NATS security proof lab."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess

import pytest

from .helpers import (
    REPO_ROOT,
    compose_down,
    compose_up,
    dedicated_resources_absent,
    docker_available,
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    cleanup_items = [item for item in items if "test_cleanup_integration" in item.nodeid]
    other_items = [item for item in items if item not in cleanup_items]
    items[:] = other_items + cleanup_items


def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config.option, "numprocesses"):
        config.option.numprocesses = 0
    markexpr = getattr(config.option, "markexpr", "") or ""
    if "not integration" in markexpr:
        return
    _ensure_nkeys()


def _ensure_nkeys() -> None:
    """JWT .creds auth in-process requires nkeys; install into the lab venv if absent."""
    if importlib.util.find_spec("nkeys") is not None:
        return
    result = subprocess.run(
        ["uv", "pip", "install", "nkeys"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    importlib.invalidate_caches()
    if result.returncode != 0 or importlib.util.find_spec("nkeys") is None:
        detail = (result.stderr or result.stdout).strip()
        msg = f"nkeys is required for NATS JWT proof connections: {detail}"
        raise RuntimeError(msg)


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
