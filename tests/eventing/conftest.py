"""Shared helpers for eventing infrastructure tests."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EVENTING_ROOT = REPO_ROOT / "infra" / "eventing"
COMPOSE_FILE = REPO_ROOT / "infra" / "compose" / "eventing-foundation.compose.yaml"


def load_topology_yaml(name: str) -> dict:
    path = EVENTING_ROOT / "topology" / name
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)
