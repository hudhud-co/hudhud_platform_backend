#!/usr/bin/env python3
"""Architecture boundary verifier for the HUDHUD platform monorepo.

Usage:
    uv run python scripts/quality/verify_boundaries.py

Exit code 0 when all checks pass; 1 when violations are found.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment, misc]

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "services"
PACKAGES_DIR = REPO_ROOT / "packages"
ARCHITECTURE_DIR = REPO_ROOT / "architecture"
PROVENANCE_FILE = REPO_ROOT / "docs" / "audit" / "legacy-provenance.yaml"
BOUNDARIES_FILE = ARCHITECTURE_DIR / "service-boundaries.yaml"

ORM_INDICATORS = {
    "sqlalchemy",
    "sqlalchemy.orm",
    "sqlalchemy.ext.declarative",
    "alembic",
}

DOMAIN_FORBIDDEN_IN_PACKAGES = {
    "domain",
    "entities",
    "aggregates",
    "value_objects",
}

LEGACY_PATH_PATTERNS = (
    re.compile(r"hudhud-backend", re.IGNORECASE),
    re.compile(r"hudhud_backend", re.IGNORECASE),
)

REQUIRED_BOUNDARY_FIELDS = (
    "display_name",
    "legacy_owner",
    "proposed_platform_owner",
    "transitional_deployable_candidate",
    "extraction_status",
    "data_ownership",
    "allowed_dependencies",
    "public_api_ownership",
)

REQUIRED_OWNERSHIP_SUBFIELDS = ("strategy",)

EXTRACTED_STATUSES = {
    "in_progress",
    "extracted",
    "cutover_ready",
}

DB_OWNERSHIP_STRATEGIES = {
    "dedicated_database",
    "read_projection",
    "none",
    "undecided",
}


@dataclass
class Violation:
    rule: str
    message: str
    path: str = ""


@dataclass
class VerificationResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, rule: str, message: str, path: str = "") -> None:
        self.violations.append(Violation(rule=rule, message=message, path=path))


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML is required; install with: uv sync --dev")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def _discover_services() -> list[Path]:
    if not SERVICES_DIR.is_dir():
        return []
    services: list[Path] = []
    for entry in sorted(SERVICES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if (entry / "pyproject.toml").exists() or any(entry.rglob("*.py")):
            services.append(entry)
    return services


def _discover_packages() -> list[Path]:
    if not PACKAGES_DIR.is_dir():
        return []
    packages: list[Path] = []
    for entry in sorted(PACKAGES_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            packages.append(entry)
    return packages


def _python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    )


def _parse_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _service_package_name(service_dir: Path) -> str:
    pyproject = service_dir / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8")
        match = re.search(r'name\s*=\s*"([^"]+)"', content)
        if match:
            return match.group(1).replace("-", "_")
    return service_dir.name.replace("-", "_")


def _declared_shared_packages(boundaries: dict) -> set[str]:
    declared: set[str] = set()
    for package_dir in _discover_packages():
        declared.add(package_dir.name.replace("-", "_"))
        pyproject = package_dir / "pyproject.toml"
        if pyproject.exists():
            match = re.search(r'name\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
            if match:
                declared.add(match.group(1).replace("-", "_"))
    shared = boundaries.get("shared_packages", {})
    for category in shared.get("allowed_categories", []):
        declared.add(str(category))
    return declared


def check_cross_service_imports(result: VerificationResult, services: list[Path]) -> None:
    """Rule 1: One service must not import another service's Python package."""
    service_names = {_service_package_name(s): s for s in services}
    for service in services:
        own_name = _service_package_name(service)
        for py_file in _python_files(service):
            for imported in _parse_imports(py_file):
                for other_name, _other_dir in service_names.items():
                    if other_name == own_name:
                        continue
                    if imported == other_name or imported.startswith(f"{other_name}."):
                        rel = py_file.relative_to(REPO_ROOT)
                        result.add(
                            "cross_service_import",
                            (
                                f"Service '{own_name}' imports '{imported}' "
                                f"from service '{other_name}'"
                            ),
                            str(rel),
                        )
                if imported.startswith("services."):
                    rel = py_file.relative_to(REPO_ROOT)
                    result.add(
                        "cross_service_import",
                        f"Direct services.* import forbidden: {imported}",
                        str(rel),
                    )


def check_packages_no_orm(result: VerificationResult, packages: list[Path]) -> None:
    """Rule 2: Shared packages must not contain ORM models or migrations."""
    for package in packages:
        for py_file in _python_files(package):
            rel = py_file.relative_to(REPO_ROOT)
            if "alembic" in py_file.parts or py_file.name.startswith("migration"):
                result.add(
                    "package_orm_or_migration",
                    "Migration file in shared package",
                    str(rel),
                )
            if py_file.name == "models.py" and "infrastructure" in py_file.parts:
                result.add(
                    "package_orm_or_migration",
                    "ORM models.py in shared package infrastructure layer",
                    str(rel),
                )
            for imported in _parse_imports(py_file):
                root_module = imported.split(".")[0]
                if root_module in ORM_INDICATORS or imported in ORM_INDICATORS:
                    result.add(
                        "package_orm_or_migration",
                        f"Shared package imports ORM/migration dependency: {imported}",
                        str(rel),
                    )
        versions = package / "alembic" / "versions"
        if versions.is_dir() and any(versions.iterdir()):
            result.add(
                "package_orm_or_migration",
                "Alembic versions directory in shared package",
                str(versions.relative_to(REPO_ROOT)),
            )


def check_service_migration_references(result: VerificationResult, services: list[Path]) -> None:
    """Rule 3: A service must not reference another service's migration directory."""
    migration_dirs = {
        s.name: s / "alembic" for s in services if (s / "alembic").is_dir()
    }
    for service in services:
        for py_file in _python_files(service):
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for other_name, _migration_dir in migration_dirs.items():
                if other_name == service.name:
                    continue
                marker = f"services/{other_name}/alembic"
                if marker in content:
                    result.add(
                        "cross_service_migration_ref",
                        f"References migration directory of service '{other_name}'",
                        str(py_file.relative_to(REPO_ROOT)),
                    )


def check_legacy_dependency(result: VerificationResult, services: list[Path]) -> None:
    """Rule 4: Services must not declare filesystem/path dependency on legacy repository."""
    targets = list(services)
    for service in services:
        targets.append(service / "pyproject.toml")
        targets.append(service / "Dockerfile")
    root_pyproject = REPO_ROOT / "pyproject.toml"
    if root_pyproject.exists():
        targets.append(root_pyproject)

    for target in targets:
        if not target.exists() or target.is_dir():
            continue
        content = target.read_text(encoding="utf-8", errors="replace")
        for pattern in LEGACY_PATH_PATTERNS:
            if pattern.search(content):
                rel = target.relative_to(REPO_ROOT)
                result.add(
                    "legacy_dependency",
                    f"Legacy repository reference found ({pattern.pattern})",
                    str(rel),
                )


def check_docker_build_context(result: VerificationResult, services: list[Path]) -> None:
    """Rule 5: Service Docker build must declare explicit allowlist when using parent context."""
    for service in services:
        dockerfile = service / "Dockerfile"
        if not dockerfile.exists():
            continue
        content = dockerfile.read_text(encoding="utf-8", errors="replace")
        copies_parent = bool(re.search(r"COPY\s+\.\.", content)) or "context: .." in content
        has_allowlist = (
            "BUILD_CONTEXT_ALLOWLIST" in content
            or "hudhud-build-allowlist" in content.lower()
            or re.search(r"COPY\s+services/", content) is not None
        )
        if copies_parent and not has_allowlist:
            result.add(
                "docker_build_context",
                "Dockerfile copies parent context without explicit BUILD_CONTEXT_ALLOWLIST",
                str(dockerfile.relative_to(REPO_ROOT)),
            )


def check_packages_no_domain_code(result: VerificationResult, packages: list[Path]) -> None:
    """Rule 6: Domain or ORM code must not be placed in packages/."""
    for package in packages:
        for py_file in _python_files(package):
            rel = py_file.relative_to(REPO_ROOT)
            parts = {part.lower() for part in py_file.parts}
            if parts & DOMAIN_FORBIDDEN_IN_PACKAGES:
                result.add(
                    "package_domain_code",
                    f"Domain-layer path segment in shared package: {py_file.parts}",
                    str(rel),
                )
            forbidden_names = {"entities.py", "aggregates.py", "repository.py", "repositories.py"}
            if py_file.name in forbidden_names and "test" not in py_file.name:
                result.add(
                    "package_domain_code",
                    f"Domain/repository module name in shared package: {py_file.name}",
                    str(rel),
                )


def check_migrations_in_owner_service(result: VerificationResult, services: list[Path]) -> None:
    """Rule 7: Migrations must live inside their owning service."""
    for service in services:
        for py_file in _python_files(service):
            if "alembic" in py_file.parts and "versions" in py_file.parts:
                continue
            if py_file.suffix == ".py" and re.search(r"alembic/versions", str(py_file)):
                continue
        alembic_root = service / "alembic"
        if not alembic_root.is_dir():
            continue
        for migration in (alembic_root / "versions").glob("*.py"):
            if migration.name == "__init__.py":
                continue

    repo_alembic = REPO_ROOT / "alembic"
    if repo_alembic.is_dir() and any((repo_alembic / "versions").glob("*.py")):
        result.add(
            "migration_outside_service",
            "Root-level alembic migrations found; migrations must be service-owned",
            "alembic/versions",
        )

    for orphan in REPO_ROOT.glob("migrations/**/*.py"):
        result.add(
            "migration_outside_service",
            "Migration file outside services/ directory",
            str(orphan.relative_to(REPO_ROOT)),
        )


def check_undeclared_package_imports(
    result: VerificationResult,
    services: list[Path],
    boundaries: dict,
) -> None:
    """Rule 8: Services must not import undeclared shared packages."""
    declared = _declared_shared_packages(boundaries)
    stdlib_and_common = {
        "abc",
        "asyncio",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "hashlib",
        "http",
        "io",
        "itertools",
        "json",
        "logging",
        "os",
        "pathlib",
        "re",
        "sys",
        "time",
        "typing",
        "uuid",
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "alembic",
        "uvicorn",
        "httpx",
        "redis",
        "pytest",
    }
    for service in services:
        for py_file in _python_files(service):
            for imported in _parse_imports(py_file):
                root = imported.split(".")[0]
                if root in stdlib_and_common:
                    continue
                if root in declared:
                    continue
                if root == "packages" or imported.startswith("packages."):
                    pkg_name = imported.split(".")[1] if "." in imported else ""
                    if pkg_name and pkg_name not in declared:
                        result.add(
                            "undeclared_package_import",
                            f"Import from undeclared shared package: {imported}",
                            str(py_file.relative_to(REPO_ROOT)),
                        )


def check_manifest_ownership(result: VerificationResult, boundaries: dict) -> None:
    """Rule 9: Every bounded context entry must declare ownership information."""
    contexts = boundaries.get("bounded_contexts", {})
    if not contexts:
        result.add(
            "manifest_ownership",
            "service-boundaries.yaml has no bounded_contexts defined",
            str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
        )
        return

    for context_id, context in contexts.items():
        if not isinstance(context, dict):
            result.add(
                "manifest_ownership",
                f"Context '{context_id}' is not a mapping",
                str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
            )
            continue
        for field_name in REQUIRED_BOUNDARY_FIELDS:
            if field_name not in context:
                result.add(
                    "manifest_ownership",
                    f"Context '{context_id}' missing required field '{field_name}'",
                    str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
                )
        ownership = context.get("data_ownership", {})
        if isinstance(ownership, dict):
            for subfield in REQUIRED_OWNERSHIP_SUBFIELDS:
                if subfield not in ownership:
                    result.add(
                        "manifest_ownership",
                        f"Context '{context_id}' data_ownership missing '{subfield}'",
                        str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
                    )


def check_database_ownership_strategy(result: VerificationResult, boundaries: dict) -> None:
    """Rule 10: Proposed extracted services must declare database ownership strategy."""
    contexts = boundaries.get("bounded_contexts", {})
    for context_id, context in contexts.items():
        if not isinstance(context, dict):
            continue
        extraction_status = context.get("extraction_status", "")
        if extraction_status not in EXTRACTED_STATUSES:
            continue
        ownership = context.get("data_ownership", {})
        if not isinstance(ownership, dict):
            result.add(
                "database_ownership_strategy",
                f"Context '{context_id}' marked for extraction but lacks data_ownership",
                str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
            )
            continue
        strategy = ownership.get("strategy", "")
        if strategy not in DB_OWNERSHIP_STRATEGIES or strategy == "undecided":
            result.add(
                "database_ownership_strategy",
                (
                    f"Extracted context '{context_id}' must declare concrete "
                    "database ownership strategy"
                ),
                str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
            )


def check_gateway_no_domain_tables(result: VerificationResult, boundaries: dict) -> None:
    """Rule 11: Gateway must not be declared as owning domain tables."""
    gateway = boundaries.get("bounded_contexts", {}).get("gateway", {})
    if not isinstance(gateway, dict):
        return
    ownership = gateway.get("data_ownership", {})
    if not isinstance(ownership, dict):
        return
    strategy = ownership.get("strategy", "")
    tables = ownership.get("legacy_tables", [])
    if strategy not in ("none", "") and tables:
        result.add(
            "gateway_domain_tables",
            "Gateway declares domain table ownership",
            str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
        )
    if tables:
        result.add(
            "gateway_domain_tables",
            f"Gateway declares legacy_tables: {tables}",
            str(BOUNDARIES_FILE.relative_to(REPO_ROOT)),
        )


def check_legacy_provenance(result: VerificationResult) -> None:
    """Rule 12: Legacy code copied into repo requires provenance record."""
    legacy_markers_dir = REPO_ROOT / "docs" / "audit" / "legacy-imports"
    if legacy_markers_dir.is_dir():
        for marker in legacy_markers_dir.rglob("*"):
            if marker.is_file() and marker.name != ".gitkeep":
                if not PROVENANCE_FILE.exists():
                    result.add(
                        "legacy_provenance",
                        "Legacy import markers exist but legacy-provenance.yaml is missing",
                        str(marker.relative_to(REPO_ROOT)),
                    )
                    return
                provenance = _load_yaml(PROVENANCE_FILE)
                records = provenance.get("records", [])
                recorded_paths = {r.get("target_path") for r in records if isinstance(r, dict)}
                rel_marker = str(marker.relative_to(REPO_ROOT))
                if rel_marker not in recorded_paths:
                    result.add(
                        "legacy_provenance",
                        f"Legacy import at '{rel_marker}' lacks provenance record",
                        rel_marker,
                    )


def verify() -> VerificationResult:
    result = VerificationResult()
    services = _discover_services()
    packages = _discover_packages()
    boundaries = _load_yaml(BOUNDARIES_FILE) if BOUNDARIES_FILE.exists() else {}

    check_cross_service_imports(result, services)
    check_packages_no_orm(result, packages)
    check_service_migration_references(result, services)
    check_legacy_dependency(result, services)
    check_docker_build_context(result, services)
    check_packages_no_domain_code(result, packages)
    check_migrations_in_owner_service(result, services)
    check_undeclared_package_imports(result, services, boundaries)
    check_manifest_ownership(result, boundaries)
    check_database_ownership_strategy(result, boundaries)
    check_gateway_no_domain_tables(result, boundaries)
    check_legacy_provenance(result)

    return result


def main() -> int:
    if yaml is None:
        print("ERROR: PyYAML required. Run: uv sync --dev", file=sys.stderr)
        return 1

    if not BOUNDARIES_FILE.exists():
        print(f"ERROR: Missing {BOUNDARIES_FILE}", file=sys.stderr)
        return 1

    result = verify()
    if result.ok:
        print("Architecture boundary verification passed.")
        return 0

    print(f"Architecture boundary verification FAILED ({len(result.violations)} violations):\n")
    for violation in result.violations:
        location = f" [{violation.path}]" if violation.path else ""
        print(f"  [{violation.rule}]{location} {violation.message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
