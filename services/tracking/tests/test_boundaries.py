"""Package and service import boundary smoke tests."""

from __future__ import annotations

import ast
from pathlib import Path


def test_no_cross_service_imports() -> None:
    service_root = Path(__file__).resolve().parents[1] / "src"
    forbidden_roots = {
        "audit",
        "shipment",
        "gateway",
        "delivery",
        "legacy_event_bridge",
        "services",
        "finance",
        "identity",
    }
    for py_file in service_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_roots
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_roots


def test_allowed_shared_packages_only() -> None:
    service_root = Path(__file__).resolve().parents[1] / "src"
    allowed = {"tracking", "event_envelope", "messaging_conformance", "nats"}
    stdlib = {
        "abc",
        "asyncio",
        "base64",
        "collections",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "json",
        "logging",
        "os",
        "pathlib",
        "re",
        "signal",
        "ssl",
        "sys",
        "time",
        "typing",
        "uuid",
        "yaml",
        "__future__",
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "alembic",
        "types",
    }
    for py_file in service_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in allowed and module not in stdlib:
                        raise AssertionError(f"Unexpected import {alias.name} in {py_file}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.split(".")[0]
                if module not in allowed and module not in stdlib:
                    raise AssertionError(f"Unexpected import {node.module} in {py_file}")


def test_does_not_import_legacy() -> None:
    service_root = Path(__file__).resolve().parents[1] / "src"
    for py_file in service_root.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "hudhud-backend" not in content
        assert "hudhud_backend" not in content
