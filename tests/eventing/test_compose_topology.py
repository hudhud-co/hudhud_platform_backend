"""Compose topology validation for eventing foundation."""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from .conftest import COMPOSE_FILE, REPO_ROOT

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_eventing_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file()


def test_compose_config_parses() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "eventing",
            "config",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    services = rendered["services"]
    assert "nats" in services
    assert "eventing-bootstrap" in services


def test_compose_uses_dedicated_eventing_network_and_volume() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "eventing",
            "config",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "hudhud_eventing" in rendered["networks"]
    assert "hudhud_eventing_jetstream" in rendered["volumes"]


def test_compose_has_no_production_source_mounts() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "eventing",
            "config",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert ".:/app" not in result.stdout


def test_nats_service_declares_single_replica_not_ha() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "eventing",
            "config",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    labels = rendered["services"]["nats"].get("labels", {})
    assert labels.get("hudhud.jetstream.replicas") == "1"
    assert labels.get("hudhud.jetstream.ha") == "false"
