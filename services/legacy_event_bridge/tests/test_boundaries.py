"""Package and service import boundary smoke tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ALLOWED_SHARED_PACKAGES = {
    "legacy_event_bridge",
    "event_envelope",
    "messaging_conformance",
    "nats",
}
ALLOWED_THIRD_PARTY_PACKAGES = {
    "alembic",
    "fastapi",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
    "yaml",
}
_STDLIB_MODULE_NAMES = sys.stdlib_module_names


def _import_root(module: str) -> str:
    return module.split(".", 1)[0]


def _is_allowed_import(module: str) -> bool:
    root = _import_root(module)
    if root in ALLOWED_SHARED_PACKAGES:
        return True
    if root in ALLOWED_THIRD_PARTY_PACKAGES:
        return True
    return root in _STDLIB_MODULE_NAMES


def test_no_cross_service_imports() -> None:
    service_root = Path(__file__).resolve().parents[1] / "src"
    forbidden_roots = {"shipment", "audit", "gateway", "delivery", "services"}
    for py_file in service_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = _import_root(alias.name)
                    assert root not in forbidden_roots
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = _import_root(node.module)
                assert root not in forbidden_roots


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("collections.abc", True),
        ("legacy_event_bridge.config", True),
        ("shipment.domain", False),
        ("unallowlisted_hudhud_package", False),
    ],
)
def test_import_allowlist_classification(module: str, expected: bool) -> None:
    assert _is_allowed_import(module) is expected


def test_allowed_shared_packages_only() -> None:
    service_root = Path(__file__).resolve().parents[1] / "src"
    for py_file in service_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not _is_allowed_import(alias.name):
                        raise AssertionError(f"Unexpected import {alias.name} in {py_file}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and not _is_allowed_import(node.module)
            ):
                raise AssertionError(f"Unexpected import {node.module} in {py_file}")
