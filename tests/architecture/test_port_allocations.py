"""Architecture tests for runtime port allocation governance."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "quality" / "verify_port_allocations.py"
REGISTRY_FILE = REPO_ROOT / "architecture" / "runtime-port-registry.yaml"


def load_port_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_port_allocations", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ports() -> ModuleType:
    return load_port_module()


@pytest.fixture(scope="module")
def canonical_registry() -> dict:
    with REGISTRY_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_verify_port_allocations_script_passes_on_canonical_registry() -> None:
    result = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), "--registry", str(REGISTRY_FILE)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Port allocation verification passed" in result.stdout


def test_valid_isolated_internal_port_reuse(ports: ModuleType) -> None:
    registry = {
        "version": 1,
        "allocations": [
            {
                "id": "svc-a-db",
                "binding_kind": "internal_container",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": False,
                        "host": {"type": "unpublished"},
                        "container": {"port": 5432},
                        "network_scope": "svc_a_net",
                    }
                ],
            },
            {
                "id": "svc-b-db",
                "binding_kind": "internal_container",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": False,
                        "host": {"type": "unpublished"},
                        "container": {"port": 5432},
                        "network_scope": "svc_b_net",
                    }
                ],
            },
        ],
    }
    result = ports.verify_registry(registry)
    assert result.ok, ports.format_violations(result.violations)


def test_duplicate_host_binding_fails(ports: ModuleType) -> None:
    registry = {
        "version": 1,
        "allocations": [
            {
                "id": "first-http",
                "binding_kind": "host_published",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 8100},
                        "container": {"port": 8000},
                    }
                ],
            },
            {
                "id": "second-http",
                "binding_kind": "host_published",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 8100},
                        "container": {"port": 8001},
                    }
                ],
            },
        ],
    }
    result = ports.verify_registry(registry)
    assert not result.ok
    assert any(v.rule == "host_collision" for v in result.violations)


def test_protocol_distinction_allows_same_numeric_port(ports: ModuleType) -> None:
    registry = {
        "version": 1,
        "allocations": [
            {
                "id": "tcp-svc",
                "binding_kind": "host_published",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 9999},
                        "container": {"port": 9999},
                    }
                ],
            },
            {
                "id": "udp-svc",
                "binding_kind": "host_published",
                "protocol": "udp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 9999},
                        "container": {"port": 9999},
                    }
                ],
            },
        ],
    }
    result = ports.verify_registry(registry)
    assert result.ok, ports.format_violations(result.violations)


def test_environment_specific_allocation_allows_same_port_across_envs(ports: ModuleType) -> None:
    registry = {
        "version": 1,
        "allocations": [
            {
                "id": "env-a",
                "binding_kind": "host_published",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "legacy_dev_local",
                        "active": True,
                        "host": {"type": "fixed", "port": 8001},
                        "container": {"port": 8001},
                    },
                    {
                        "environment": "legacy_production",
                        "active": True,
                        "host": {"type": "loopback", "port": 8001},
                        "container": {"port": 8001},
                    },
                ],
            }
        ],
    }
    result = ports.verify_registry(registry)
    assert result.ok, ports.format_violations(result.violations)


def test_configurable_and_unresolved_bindings_skip_collision(ports: ModuleType) -> None:
    registry = {
        "version": 1,
        "allocations": [
            {
                "id": "configurable-a",
                "binding_kind": "environment_configurable",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "env_var", "name": "HTTP_PORT"},
                        "container": {"type": "env_var", "name": "HTTP_PORT"},
                    }
                ],
            },
            {
                "id": "unresolved-b",
                "binding_kind": "unresolved",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "unresolved"},
                        "container": {"type": "unresolved"},
                    }
                ],
            },
            {
                "id": "fixed-c",
                "binding_kind": "host_published",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 8200},
                        "container": {"port": 8000},
                    }
                ],
            },
        ],
    }
    result = ports.verify_registry(registry)
    assert result.ok, ports.format_violations(result.violations)


def test_port_range_overlap_fails(ports: ModuleType) -> None:
    registry = {
        "version": 1,
        "allocations": [
            {
                "id": "range-a",
                "binding_kind": "staging_reservation",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "range", "start": 8100, "end": 8120},
                        "container": {"type": "range", "start": 8000, "end": 8020},
                    }
                ],
            },
            {
                "id": "range-b",
                "binding_kind": "staging_reservation",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "range", "start": 8110, "end": 8130},
                        "container": {"type": "range", "start": 8010, "end": 8030},
                    }
                ],
            },
        ],
    }
    result = ports.verify_registry(registry)
    assert not result.ok
    assert any(v.rule == "host_collision" for v in result.violations)


def test_malformed_registry_entry_fails(ports: ModuleType) -> None:
    registry = {
        "version": 1,
        "allocations": [
            {
                "id": "",
                "binding_kind": "not_a_kind",
                "protocol": "icmp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 70000},
                        "container": {"port": 8000},
                    }
                ],
            }
        ],
    }
    result = ports.verify_registry(registry)
    assert not result.ok
    assert any(v.rule == "schema" for v in result.violations)


def test_deterministic_diagnostics_order(ports: ModuleType) -> None:
    registry = {
        "version": 2,
        "allocations": [
            {
                "id": "z-last",
                "binding_kind": "host_published",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 8100},
                        "container": {"port": 8000},
                    }
                ],
            },
            {
                "id": "a-first",
                "binding_kind": "host_published",
                "protocol": "tcp",
                "bindings": [
                    {
                        "environment": "platform_local_dev",
                        "active": True,
                        "host": {"type": "fixed", "port": 8100},
                        "container": {"port": 8001},
                    }
                ],
            },
        ],
    }
    result = ports.verify_registry(registry)
    formatted_once = ports.format_violations(result.violations)
    formatted_twice = ports.format_violations(result.violations)
    assert formatted_once == formatted_twice
    assert "[host_collision]" in formatted_once


def test_canonical_registry_documents_legacy_bindings(canonical_registry: dict) -> None:
    allocations = {item["id"]: item for item in canonical_registry["allocations"]}
    legacy_http = allocations["legacy-monolith-http"]
    dev_binding = next(
        b for b in legacy_http["bindings"] if b["environment"] == "legacy_dev_local"
    )
    assert dev_binding["host"]["port"] == 8001
    assert dev_binding["container"]["port"] == 8001

    legacy_pg = allocations["legacy-postgres"]
    dev_pg = next(b for b in legacy_pg["bindings"] if b["environment"] == "legacy_dev_local")
    assert dev_pg["host"]["port"] == 5433
    assert dev_pg["container"]["port"] == 5432
