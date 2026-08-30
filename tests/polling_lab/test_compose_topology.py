"""Static validation for polling lab Compose topology and strategy matrix."""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from .conftest import COMPOSE_FILE, LAB_ROOT, MATRIX_FILE, REPO_ROOT, load_matrix
from .scenarios import SCENARIO_IDS

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_lab_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file()


def test_strategy_matrix_exists() -> None:
    assert MATRIX_FILE.is_file()


def test_matrix_covers_all_scenarios() -> None:
    matrix = load_matrix()
    assert set(matrix["scenarios"]) == set(SCENARIO_IDS)
    assert set(matrix["expected_outcomes"]) == set(SCENARIO_IDS)


def test_matrix_strategy_keys_consistent() -> None:
    matrix = load_matrix()
    strategy_names = set(matrix["strategies"])
    for scenario_id, outcomes in matrix["expected_outcomes"].items():
        assert set(outcomes) == strategy_names, scenario_id


def test_compose_config_parses() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "-p",
            "hudhud-legacy-polling-lab",
            "--profile",
            "polling-lab",
            "config",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "postgres" in rendered["services"]


def test_compose_uses_dedicated_network_volume_no_host_ports() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "-p",
            "hudhud-legacy-polling-lab",
            "--profile",
            "polling-lab",
            "config",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "hudhud_polling_lab" in rendered["networks"]
    assert "hudhud_polling_lab_pgdata" in rendered["volumes"]
    postgres = rendered["services"]["postgres"]
    assert "ports" not in postgres


def test_compose_has_no_production_source_mounts() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "-p",
            "hudhud-legacy-polling-lab",
            "--profile",
            "polling-lab",
            "config",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert ".:/app" not in result.stdout
    assert "hudhud-backend" not in result.stdout


def test_lab_report_exists() -> None:
    report = LAB_ROOT / "REPORT.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "HUDHUD_W3_B_POLLING_LAB" in text
    assert "ADR-0007" in text
