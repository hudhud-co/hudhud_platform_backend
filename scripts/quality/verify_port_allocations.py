#!/usr/bin/env python3
"""Verify HUDHUD runtime port registry for host-binding collisions and schema validity.

Usage:
    uv run python scripts/quality/verify_port_allocations.py
    uv run python scripts/quality/verify_port_allocations.py --registry path/to/registry.yaml

Exit code 0 when all checks pass; 1 when violations are found.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment, misc]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO_ROOT / "architecture" / "runtime-port-registry.yaml"

VALID_PROTOCOLS = frozenset({"tcp", "udp"})
VALID_BINDING_KINDS = frozenset(
    {
        "internal_container",
        "host_published",
        "developer_local_default",
        "staging_reservation",
        "production_exposure",
        "environment_configurable",
        "legacy_known",
        "unresolved",
    }
)
VALID_HOST_TYPES = frozenset(
    {
        "fixed",
        "loopback",
        "range",
        "unpublished",
        "env_var",
        "unresolved",
    }
)
VALID_CONTAINER_TYPES = frozenset({"fixed", "range", "env_var", "unresolved"})
SKIP_COLLISION_HOST_TYPES = frozenset({"unpublished", "env_var", "unresolved"})


@dataclass(frozen=True)
class PortInterval:
    start: int
    end: int

    def overlaps(self, other: PortInterval) -> bool:
        return self.start <= other.end and other.start <= self.end

    def __str__(self) -> str:
        if self.start == self.end:
            return str(self.start)
        return f"{self.start}-{self.end}"


@dataclass
class Violation:
    rule: str
    message: str
    path: str = ""

    def sort_key(self) -> tuple[str, str, str]:
        return (self.rule, self.path, self.message)


@dataclass
class VerificationResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, rule: str, message: str, path: str = "") -> None:
        self.violations.append(Violation(rule=rule, message=message, path=path))


def _require_mapping(
    value: Any,
    label: str,
    path: str,
    result: VerificationResult,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        result.add("schema", f"{label} must be a mapping", path)
        return {}
    return value


def _require_list(value: Any, label: str, path: str, result: VerificationResult) -> list[Any]:
    if not isinstance(value, list):
        result.add("schema", f"{label} must be a list", path)
        return []
    return value


def _parse_port(value: Any, label: str, path: str, result: VerificationResult) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        result.add("schema", f"{label} must be an integer port", path)
        return None
    if value < 1 or value > 65535:
        result.add("schema", f"{label} must be between 1 and 65535", path)
        return None
    return value


def _parse_interval(
    mapping: dict[str, Any],
    *,
    prefix: str,
    path: str,
    result: VerificationResult,
) -> PortInterval | None:
    host_type = mapping.get("type", "fixed")
    if host_type in {"env_var", "unresolved", "unpublished"}:
        return None
    if host_type != "range":
        port = _parse_port(mapping.get("port"), f"{prefix}.port", path, result)
        return None if port is None else PortInterval(port, port)

    start = _parse_port(mapping.get("start"), f"{prefix}.start", path, result)
    end = _parse_port(mapping.get("end"), f"{prefix}.end", path, result)
    if start is None or end is None:
        return None
    if start > end:
        result.add("schema", f"{prefix} range start must be <= end", path)
        return None
    return PortInterval(start, end)


def _host_interval(
    host: dict[str, Any],
    *,
    path: str,
    result: VerificationResult,
) -> PortInterval | None:
    host_type = host.get("type")
    if host_type in SKIP_COLLISION_HOST_TYPES or host_type == "unpublished":
        return None
    if host_type not in VALID_HOST_TYPES:
        result.add("schema", f"invalid host.type '{host_type}'", path)
        return None
    if host_type in {"fixed", "loopback"}:
        port = _parse_port(host.get("port"), "host.port", path, result)
        if port is None:
            return None
        return PortInterval(port, port)
    if host_type == "range":
        return _parse_interval(host, prefix="host", path=path, result=result)
    return None


def _validate_allocation(
    allocation: Any,
    index: int,
    result: VerificationResult,
) -> dict[str, Any] | None:
    path = f"allocations[{index}]"
    allocation = _require_mapping(allocation, "allocation", path, result)
    if not allocation:
        return None

    alloc_id = allocation.get("id")
    if not isinstance(alloc_id, str) or not alloc_id.strip():
        result.add("schema", "allocation.id must be a non-empty string", path)

    protocol = allocation.get("protocol")
    if protocol not in VALID_PROTOCOLS:
        result.add("schema", f"allocation.protocol must be one of {sorted(VALID_PROTOCOLS)}", path)

    binding_kind = allocation.get("binding_kind")
    if binding_kind not in VALID_BINDING_KINDS:
        result.add(
            "schema",
            f"allocation.binding_kind must be one of {sorted(VALID_BINDING_KINDS)}",
            path,
        )

    bindings = _require_list(allocation.get("bindings"), "allocation.bindings", path, result)
    for binding_index, binding in enumerate(bindings):
        binding_path = f"{path}.bindings[{binding_index}]"
        binding_map = _require_mapping(binding, "binding", binding_path, result)
        if not binding_map:
            continue
        environment = binding_map.get("environment")
        if not isinstance(environment, str) or not environment.strip():
            result.add("schema", "binding.environment must be a non-empty string", binding_path)

        host = _require_mapping(binding_map.get("host"), "binding.host", binding_path, result)
        if host:
            host_type = host.get("type")
            if host_type not in VALID_HOST_TYPES:
                result.add("schema", f"invalid host.type '{host_type}'", binding_path)
            elif host_type == "env_var":
                env_name = host.get("name")
                if not isinstance(env_name, str) or not env_name.strip():
                    result.add("schema", "host.name required for env_var host type", binding_path)
            elif host_type in {"fixed", "loopback"}:
                _parse_port(host.get("port"), "host.port", binding_path, result)
            elif host_type == "range":
                _parse_interval(host, prefix="host", path=binding_path, result=result)

        container = binding_map.get("container")
        if container is not None:
            container_map = _require_mapping(container, "binding.container", binding_path, result)
            if container_map:
                container_type = container_map.get("type", "fixed")
                if container_type not in VALID_CONTAINER_TYPES and "port" in container_map:
                    _parse_port(container_map.get("port"), "container.port", binding_path, result)
                elif container_type == "range":
                    _parse_interval(
                        container_map, prefix="container", path=binding_path, result=result
                    )

    return allocation


def _collect_active_host_intervals(
    registry: dict[str, Any],
    result: VerificationResult,
) -> dict[tuple[str, str], list[tuple[str, PortInterval]]]:
    """Map (environment, protocol) -> list of (allocation_id, interval)."""
    grouped: dict[tuple[str, str], list[tuple[str, PortInterval]]] = {}
    allocations = registry.get("allocations", [])
    if not isinstance(allocations, list):
        result.add("schema", "allocations must be a list", "allocations")
        return grouped

    seen_ids: set[str] = set()
    for index, allocation in enumerate(allocations):
        validated = _validate_allocation(allocation, index, result)
        if not validated:
            continue
        alloc_id = validated.get("id", f"<index:{index}>")
        if isinstance(alloc_id, str):
            if alloc_id in seen_ids:
                result.add("duplicate_id", f"duplicate allocation id '{alloc_id}'", alloc_id)
            seen_ids.add(alloc_id)

        protocol = validated.get("protocol", "tcp")
        bindings = validated.get("bindings", [])
        if not isinstance(bindings, list):
            continue
        for binding_index, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                continue
            binding_path = f"allocations[{index}].bindings[{binding_index}]"
            if not binding.get("active", False):
                continue
            environment = binding.get("environment")
            if not isinstance(environment, str):
                continue
            host = binding.get("host")
            if not isinstance(host, dict):
                continue
            interval = _host_interval(host, path=binding_path, result=result)
            if interval is None:
                continue
            key = (environment, str(protocol))
            grouped.setdefault(key, []).append((str(alloc_id), interval))

    return grouped


def check_host_collisions(
    registry: dict[str, Any],
    result: VerificationResult,
) -> None:
    grouped = _collect_active_host_intervals(registry, result)
    for (environment, protocol), entries in sorted(grouped.items()):
        for left_index in range(len(entries)):
            left_id, left_interval = entries[left_index]
            for right_index in range(left_index + 1, len(entries)):
                right_id, right_interval = entries[right_index]
                if left_interval.overlaps(right_interval):
                    result.add(
                        "host_collision",
                        (
                            f"environment '{environment}' protocol {protocol}: "
                            f"'{left_id}' host {left_interval} overlaps "
                            f"'{right_id}' host {right_interval}"
                        ),
                        environment,
                    )


def check_internal_container_reuse_allowed(
    registry: dict[str, Any],
    result: VerificationResult,
) -> None:
    """Informational schema gate: internal-only bindings must not claim host publish."""
    allocations = registry.get("allocations", [])
    if not isinstance(allocations, list):
        return
    for index, allocation in enumerate(allocations):
        if not isinstance(allocation, dict):
            continue
        if allocation.get("binding_kind") != "internal_container":
            continue
        bindings = allocation.get("bindings", [])
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            host = binding.get("host")
            if not isinstance(host, dict):
                continue
            host_type = host.get("type")
            if host_type in {"fixed", "loopback", "range"} and binding.get("active", False):
                alloc_id = allocation.get("id", f"allocations[{index}]")
                result.add(
                    "internal_container_host_publish",
                    (
                        f"'{alloc_id}' is internal_container but has active host binding "
                        f"type '{host_type}'"
                    ),
                    str(alloc_id),
                )


def verify_registry(registry: dict[str, Any]) -> VerificationResult:
    result = VerificationResult()
    if not isinstance(registry, dict):
        result.add("schema", "registry root must be a mapping", "registry")
        return result

    version = registry.get("version")
    if version != 1:
        result.add("schema", "registry.version must be 1", "version")

    allocations = registry.get("allocations")
    if not isinstance(allocations, list) or not allocations:
        result.add("schema", "allocations must be a non-empty list", "allocations")
        return result

    for index, allocation in enumerate(allocations):
        _validate_allocation(allocation, index, result)

    check_internal_container_reuse_allowed(registry, result)
    check_host_collisions(registry, result)
    return result


def load_registry(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required. Run: uv sync --dev")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Registry root must be a mapping: {path}")
    return data


def format_violations(violations: list[Violation]) -> str:
    lines: list[str] = []
    for violation in sorted(violations, key=lambda item: item.sort_key()):
        location = f" [{violation.path}]" if violation.path else ""
        lines.append(f"  [{violation.rule}]{location} {violation.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if yaml is None:
        print("ERROR: PyYAML required. Run: uv sync --dev", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description="Verify runtime port registry allocations.")
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help=f"Path to runtime port registry YAML (default: {DEFAULT_REGISTRY})",
    )
    args = parser.parse_args(argv)

    registry_path = args.registry.resolve()
    if not registry_path.is_file():
        print(f"ERROR: Registry not found: {registry_path}", file=sys.stderr)
        return 1

    try:
        registry = load_registry(registry_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:  # type: ignore[union-attr]
        print(f"ERROR: Failed to load registry: {exc}", file=sys.stderr)
        return 1

    result = verify_registry(registry)
    if result.ok:
        print(f"Port allocation verification passed ({registry_path}).")
        return 0

    print(
        f"Port allocation verification FAILED ({len(result.violations)} violations) "
        f"for {registry_path}:\n"
    )
    print(format_violations(result.violations))
    return 1


if __name__ == "__main__":
    sys.exit(main())
