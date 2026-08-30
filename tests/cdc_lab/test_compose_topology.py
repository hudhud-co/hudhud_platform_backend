"""Compose topology validation for the legacy CDC lab."""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from .helpers import COMPOSE_FILE, compose, docker_available

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_cdc_lab_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file()


def test_compose_config_parses() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "postgres" in rendered["services"]


def test_compose_uses_dedicated_lab_network_and_volume() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "hudhud_cdc_lab" in rendered["networks"]
    assert "hudhud_cdc_lab_pgdata" in rendered["volumes"]


def test_compose_has_no_host_ports() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    postgres = rendered["services"]["postgres"]
    assert postgres.get("ports") in (None, [])


def test_compose_has_no_production_source_mounts() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    assert ".:/app" not in result.stdout
    assert "hudhud-backend" not in result.stdout


def test_postgres_declares_logical_decoding_and_single_node() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    command = rendered["services"]["postgres"].get("command", [])
    command_text = " ".join(command) if isinstance(command, list) else str(command)
    assert "wal_level=logical" in command_text
    labels = rendered["services"]["postgres"].get("labels", {})
    assert labels.get("hudhud.cdc.ha") == "false"
    assert labels.get("hudhud.cdc.plugin") == "test_decoding"


def test_cleanup_script_is_executable() -> None:
    cleanup = COMPOSE_FILE.parent / "scripts" / "cleanup.sh"
    assert cleanup.is_file()
    if shutil.which("sh") is None:
        pytest.skip("sh not available")
    result = subprocess.run(["sh", "-n", str(cleanup)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
