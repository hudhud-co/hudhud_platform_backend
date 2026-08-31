"""Package boundary smoke test."""

from __future__ import annotations

import ast
from pathlib import Path

import messaging_conformance


def test_public_api_exports_version() -> None:
    assert messaging_conformance.__version__ == "0.1.0"
    assert "decide_inbox_duplicate_delivery" in messaging_conformance.__all__


def test_package_has_no_orm_imports() -> None:
    package_root = Path(messaging_conformance.__file__).resolve().parent
    forbidden_roots = {"sqlalchemy", "alembic", "nats", "asyncpg"}
    for py_file in package_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_roots, f"{py_file}: {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_roots, f"{py_file}: {node.module}"
