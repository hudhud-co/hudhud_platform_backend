"""Shared paths and Compose helpers for the polling lab."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = REPO_ROOT / "infra" / "labs" / "legacy-polling"
COMPOSE_FILE = LAB_ROOT / "compose.yaml"
MATRIX_FILE = LAB_ROOT / "strategies" / "matrix.yaml"
COMPOSE_PROJECT = "hudhud-legacy-polling-lab"
COMPOSE_PROFILE = "polling-lab"


def load_matrix() -> dict:
    with MATRIX_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)
