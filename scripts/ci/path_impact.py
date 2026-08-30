"""Architecture-aware CI path impact calculation for the HUDHUD platform."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

OUTPUT_VERSION = 1

EXECUTABLE_OR_CONFIG_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".sh",
        ".bash",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".ini",
        ".cfg",
        ".env.example",
        ".dockerfile",
        ".sql",
        ".lock",
    }
)

EXECUTABLE_OR_CONFIG_NAMES = frozenset(
    {
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Makefile",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        ".editorconfig",
        ".gitignore",
        "AGENTS.md",
    }
)


class ImpactCategory(StrEnum):
    SERVICE = "service"
    PACKAGE = "package"
    CONTRACTS = "contracts"
    ARCHITECTURE = "architecture"
    GOVERNANCE = "governance"
    INFRASTRUCTURE = "infrastructure"
    CI_TOOLING = "ci_tooling"
    DOCS_ONLY = "docs_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PathChange:
    path: str
    status: str

    @property
    def is_deletion(self) -> bool:
        return self.status.startswith("D")

    @property
    def is_rename(self) -> bool:
        return self.status.startswith("R")


@dataclass
class PathClassification:
    path: str
    category: ImpactCategory
    service: str | None = None
    package: str | None = None


@dataclass
class ImpactResult:
    version: int = OUTPUT_VERSION
    base_sha: str | None = None
    head_sha: str | None = None
    fail_safe: bool = False
    fail_safe_reasons: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    path_changes: list[PathChange] = field(default_factory=list)
    classifications: list[PathClassification] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    affected_packages: list[str] = field(default_factory=list)
    impact_flags: dict[str, bool] = field(default_factory=dict)
    unknown_paths: list[str] = field(default_factory=list)
    docs_only: bool = False
    full_validation: bool = True
    run_architecture_gates: bool = True
    run_governance_gates: bool = True
    run_quality_gates: bool = True
    run_service_scoped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "fail_safe": self.fail_safe,
            "fail_safe_reasons": sorted(self.fail_safe_reasons),
            "changed_paths": sorted(self.changed_paths),
            "path_changes": [
                {"path": change.path, "status": change.status}
                for change in sorted(self.path_changes, key=lambda item: item.path)
            ],
            "classifications": [
                {
                    "path": item.path,
                    "category": item.category.value,
                    "service": item.service,
                    "package": item.package,
                }
                for item in sorted(self.classifications, key=lambda item: item.path)
            ],
            "impact": {
                "affected_services": sorted(self.affected_services),
                "affected_packages": sorted(self.affected_packages),
                "contracts": self.impact_flags.get("contracts", False),
                "architecture": self.impact_flags.get("architecture", False),
                "governance": self.impact_flags.get("governance", False),
                "infrastructure": self.impact_flags.get("infrastructure", False),
                "ci_tooling": self.impact_flags.get("ci_tooling", False),
                "docs_only": self.docs_only,
                "unknown_paths": sorted(self.unknown_paths),
            },
            "validation_scope": {
                "full_validation": self.full_validation,
                "run_architecture_gates": self.run_architecture_gates,
                "run_governance_gates": self.run_governance_gates,
                "run_quality_gates": self.run_quality_gates,
                "run_service_scoped": self.run_service_scoped,
                "affected_services": sorted(self.affected_services),
                "affected_packages": sorted(self.affected_packages),
            },
        }


def discover_services(repo_root: Path = REPO_ROOT) -> list[str]:
    services_dir = repo_root / "services"
    if not services_dir.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(services_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name == "__pycache__":
            continue
        if (
            (entry / "pyproject.toml").exists()
            or any(entry.rglob("*.py"))
            or ((entry / "README.md").exists() and entry.name != "README.md")
        ):
            names.append(entry.name)
    return names


def discover_packages(repo_root: Path = REPO_ROOT) -> list[str]:
    packages_dir = repo_root / "packages"
    if not packages_dir.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(packages_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            names.append(entry.name)
    return names


def normalize_repo_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_executable_or_config_path(path: str) -> bool:
    name = Path(path).name
    if name in EXECUTABLE_OR_CONFIG_NAMES:
        return True
    suffix = Path(path).suffix.lower()
    return suffix in EXECUTABLE_OR_CONFIG_SUFFIXES


def is_docs_only_path(path: str) -> bool:
    normalized = normalize_repo_path(path)
    if normalized == "README.md":
        return True
    if normalized.endswith(".md") and normalized.startswith("docs/"):
        return not normalized.startswith("docs/adr/")
    return False


def classify_path(
    path: str,
    *,
    known_services: set[str] | None = None,
) -> PathClassification:
    normalized = normalize_repo_path(path)
    services = known_services or set(discover_services())
    category = ImpactCategory.UNKNOWN
    service: str | None = None
    package: str | None = None

    prefix_rules: list[tuple[str, ImpactCategory]] = [
        ("contracts/", ImpactCategory.CONTRACTS),
        ("architecture/", ImpactCategory.ARCHITECTURE),
        ("docs/adr/", ImpactCategory.GOVERNANCE),
        ("infra/", ImpactCategory.INFRASTRUCTURE),
        (".github/", ImpactCategory.CI_TOOLING),
        ("scripts/", ImpactCategory.CI_TOOLING),
    ]
    for prefix, mapped in prefix_rules:
        if normalized.startswith(prefix):
            category = mapped
            break
    else:
        if normalized.startswith(".cursor/") or normalized == "AGENTS.md":
            category = ImpactCategory.GOVERNANCE
        elif normalized in {
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".editorconfig",
            ".gitignore",
        }:
            category = ImpactCategory.CI_TOOLING
        else:
            service_match = re.match(r"^services/([^/]+)/", normalized)
            package_match = re.match(r"^packages/([^/]+)/", normalized)
            tests_match = re.match(r"^tests/([^/]+)/", normalized)
            if service_match:
                category = ImpactCategory.SERVICE
                service = service_match.group(1)
            elif package_match:
                category = ImpactCategory.PACKAGE
                package = package_match.group(1)
            elif tests_match:
                test_area = tests_match.group(1)
                if test_area in {"architecture", "governance"}:
                    category = ImpactCategory.GOVERNANCE
                elif test_area == "ci":
                    category = ImpactCategory.CI_TOOLING
                elif test_area in services:
                    category = ImpactCategory.SERVICE
                    service = test_area
            elif is_docs_only_path(normalized):
                category = ImpactCategory.DOCS_ONLY

    return PathClassification(
        path=normalized,
        category=category,
        service=service,
        package=package,
    )


def parse_changed_files(lines: list[str]) -> list[PathChange]:
    changes: list[PathChange] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == 1:
            changes.append(PathChange(path=normalize_repo_path(parts[0]), status="M"))
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            old_path = normalize_repo_path(parts[1])
            new_path = normalize_repo_path(parts[2])
            changes.append(PathChange(path=old_path, status=status))
            changes.append(PathChange(path=new_path, status=f"{status}_NEW"))
            continue
        path = normalize_repo_path(parts[-1])
        changes.append(PathChange(path=path, status=status))
    return changes


def git_diff_name_status(base_sha: str, head_sha: str, repo_root: Path = REPO_ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-status", f"{base_sha}...{head_sha}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return [line for line in result.stdout.splitlines() if line.strip()]


def git_object_exists(revision: str, repo_root: Path = REPO_ROOT) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def calculate_impact(
    *,
    base_sha: str | None = None,
    head_sha: str | None = None,
    changed_lines: list[str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> ImpactResult:
    result = ImpactResult(base_sha=base_sha, head_sha=head_sha)
    known_services = set(discover_services(repo_root))

    if changed_lines is not None:
        path_changes = parse_changed_files(changed_lines)
    else:
        if not base_sha:
            result.fail_safe = True
            result.fail_safe_reasons.append("missing_base_sha")
            result.full_validation = True
            return _finalize_scope(result)

        if not git_object_exists(base_sha, repo_root):
            result.fail_safe = True
            result.fail_safe_reasons.append("base_sha_unavailable")
            result.full_validation = True
            return _finalize_scope(result)

        effective_head = head_sha or "HEAD"
        if head_sha and not git_object_exists(head_sha, repo_root):
            result.fail_safe = True
            result.fail_safe_reasons.append("head_sha_unavailable")
            result.full_validation = True
            return _finalize_scope(result)

        try:
            diff_lines = git_diff_name_status(base_sha, effective_head, repo_root)
        except RuntimeError as exc:
            result.fail_safe = True
            result.fail_safe_reasons.append("git_diff_failed")
            result.fail_safe_reasons.append(str(exc))
            result.full_validation = True
            return _finalize_scope(result)

        path_changes = parse_changed_files(diff_lines)

    if not path_changes:
        result.docs_only = True
        result.full_validation = False
        result.run_service_scoped = False
        return _finalize_scope(result)

    result.path_changes = path_changes
    result.changed_paths = sorted({change.path for change in path_changes})

    categories: set[ImpactCategory] = set()
    for change in path_changes:
        classification = classify_path(
            change.path,
            known_services=known_services,
        )
        result.classifications.append(classification)
        categories.add(classification.category)

        if classification.service:
            result.affected_services.append(classification.service)
        if classification.package:
            result.affected_packages.append(classification.package)

        if classification.category == ImpactCategory.UNKNOWN:
            result.unknown_paths.append(classification.path)
            if is_executable_or_config_path(classification.path):
                result.fail_safe_reasons.append(f"unknown_executable_path:{classification.path}")
            else:
                result.fail_safe_reasons.append(f"unknown_unmapped_path:{classification.path}")

    result.affected_services = sorted(set(result.affected_services))
    result.affected_packages = sorted(set(result.affected_packages))
    result.unknown_paths = sorted(set(result.unknown_paths))

    result.impact_flags = {
        "contracts": ImpactCategory.CONTRACTS in categories,
        "architecture": ImpactCategory.ARCHITECTURE in categories,
        "governance": ImpactCategory.GOVERNANCE in categories,
        "infrastructure": ImpactCategory.INFRASTRUCTURE in categories,
        "ci_tooling": ImpactCategory.CI_TOOLING in categories,
    }

    non_docs_categories = categories - {ImpactCategory.DOCS_ONLY}
    result.docs_only = bool(categories) and non_docs_categories == set()

    if result.unknown_paths:
        result.fail_safe = True

    if result.fail_safe_reasons:
        result.fail_safe_reasons = sorted(set(result.fail_safe_reasons))
        if any(
            reason.startswith(("unknown_", "missing_", "base_sha_", "head_sha_", "git_diff_"))
            for reason in result.fail_safe_reasons
        ):
            result.fail_safe = True

    if any(
        (
            result.impact_flags["architecture"],
            result.impact_flags["governance"],
            result.impact_flags["contracts"],
            result.impact_flags["infrastructure"],
            result.impact_flags["ci_tooling"],
            result.affected_packages,
            any(change.path in {"pyproject.toml", "uv.lock"} for change in path_changes),
        )
    ):
        result.fail_safe = True
        if result.affected_packages:
            result.fail_safe_reasons.append("shared_package_change")
        if result.impact_flags["contracts"]:
            result.fail_safe_reasons.append("contract_change")
        if result.impact_flags["architecture"]:
            result.fail_safe_reasons.append("architecture_change")
        if result.impact_flags["governance"]:
            result.fail_safe_reasons.append("governance_change")
        if result.impact_flags["infrastructure"]:
            result.fail_safe_reasons.append("infrastructure_change")
        if result.impact_flags["ci_tooling"]:
            result.fail_safe_reasons.append("ci_tooling_change")
        if any(change.path in {"pyproject.toml", "uv.lock"} for change in path_changes):
            result.fail_safe_reasons.append("dependency_lock_change")

    if result.fail_safe:
        result.full_validation = True
        result.run_service_scoped = bool(result.affected_services)
    elif result.docs_only:
        result.full_validation = False
        result.run_service_scoped = False
    else:
        result.full_validation = False
        result.run_service_scoped = bool(result.affected_services)

    return _finalize_scope(result)


def _finalize_scope(result: ImpactResult) -> ImpactResult:
    result.run_architecture_gates = True
    result.run_governance_gates = True
    result.run_quality_gates = True
    result.fail_safe_reasons = sorted(set(result.fail_safe_reasons))
    return result


def render_json(result: ImpactResult) -> str:
    return json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"


def render_github_output(result: ImpactResult) -> str:
    lines = [
        f"full_validation={'true' if result.full_validation else 'false'}",
        f"fail_safe={'true' if result.fail_safe else 'false'}",
        f"docs_only={'true' if result.docs_only else 'false'}",
        f"run_service_scoped={'true' if result.run_service_scoped else 'false'}",
        f"affected_services={json.dumps(sorted(result.affected_services))}",
        f"affected_packages={json.dumps(sorted(result.affected_packages))}",
    ]
    return "\n".join(lines) + "\n"


def write_github_output(result: ImpactResult, output_path: Path) -> None:
    payload = json.dumps(result.to_dict(), sort_keys=True)
    lines = render_github_output(result).splitlines()
    with output_path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{line}\n")
        handle.write("impact_json<<EOF\n")
        handle.write(f"{payload}\n")
        handle.write("EOF\n")
