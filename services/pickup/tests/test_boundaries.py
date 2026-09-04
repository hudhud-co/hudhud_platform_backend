"""Package and service import boundary smoke tests."""

from __future__ import annotations

import ast
from pathlib import Path


def test_no_cross_service_imports() -> None:
    service_root = Path(__file__).resolve().parents[1] / "src"
    forbidden_roots = {
        "audit",
        "tracking",
        "gateway",
        "delivery",
        "legacy_event_bridge",
        "services",
        "finance",
        "identity",
        "shipment",
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
    allowed = {
        "pickup",
        "event_envelope",
        "jsonschema",
        "messaging_conformance",
        "nats",
        "referencing",
        "yaml",
    }
    stdlib = {
        "abc",
        "asyncio",
        "collections",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
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
        "threading",
        "time",
        "typing",
        "uuid",
        "__future__",
        "types",
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "alembic",
        "uvicorn",
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
