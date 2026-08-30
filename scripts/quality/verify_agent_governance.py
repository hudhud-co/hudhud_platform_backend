#!/usr/bin/env python3
"""Verify HUDHUD Cursor agent governance assets.

Usage:
    uv run python scripts/quality/verify_agent_governance.py

Exit code 0 when all checks pass; 1 when violations are found.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment, misc]

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_FILE = REPO_ROOT / "AGENTS.md"
CURSOR_DIR = REPO_ROOT / ".cursor"
RULES_DIR = CURSOR_DIR / "rules"
SKILLS_DIR = CURSOR_DIR / "skills"

REQUIRED_RULE_SECTIONS_MIN_BODY = 1

ALWAYS_APPLY_RULES = (
    "00-repository-authority.mdc",
    "01-legacy-read-only.mdc",
    "02-architecture-boundaries.mdc",
    "03-git-worktree-safety.mdc",
    "07-security-and-secrets.mdc",
    "08-testing-and-evidence-gates.mdc",
)

SCOPED_RULES: dict[str, tuple[str, ...]] = {
    "04-python-service-quality.mdc": (".py", "services", "packages", "tests"),
    "05-database-migrations.mdc": ("alembic",),
    "06-events-and-messaging.mdc": ("contracts", "outbox", "inbox"),
    "09-adr-and-documentation.mdc": ("docs", "architecture"),
}

REQUIRED_SKILL_SECTIONS = (
    "Purpose",
    "When to use",
    "When not to use",
    "Required inputs",
    "Preconditions",
    "Procedure",
    "Allowed files or ownership scope",
    "Required validation",
    "Stop conditions",
    "Prohibited actions",
    "Output contract",
    "Completion marker",
)

SKILL_COMPLETION_MARKERS = {
    "legacy-evidence-audit": "HUDHUD_LEGACY_EVIDENCE_AUDIT_COMPLETE",
    "prepare-adr": "HUDHUD_PREPARE_ADR_COMPLETE",
    "parallel-worktree-wave": "HUDHUD_PARALLEL_WORKTREE_WAVE_READY",
    "bootstrap-service": "HUDHUD_BOOTSTRAP_SERVICE_COMPLETE",
    "define-event-contract": "HUDHUD_EVENT_CONTRACT_DEFINED",
    "create-service-migration": "HUDHUD_SERVICE_MIGRATION_PROVEN",
    "plan-extraction-cutover": "HUDHUD_EXTRACTION_CUTOVER_PLAN_READY",
    "verify-compose-topology": "HUDHUD_COMPOSE_TOPOLOGY_VERIFIED",
    "integration-gate": "HUDHUD_INTEGRATION_GATE_COMPLETE",
}

REQUIRED_PROHIBITION_LINES = (
    "Push unless the current human instruction explicitly authorizes it",
    "Pull request creation unless the current human instruction explicitly authorizes it",
    "Production access or live production mutation",
    "Destructive Git operations",
)

CANONICAL_DOCS = (
    "architecture/invariants.md",
    "architecture/service-boundaries.yaml",
    "architecture/ownership-matrix.yaml",
    "docs/adr/0000-template.md",
    "docs/audit/legacy-provenance.yaml",
)

LEGACY_ABSOLUTE_PATH = "/Users/mohammadakbari/Development/Projects/Python/hudhud-backend"
LEGACY_DIRTY_FILE = "scripts/dev_pickup_driver_simulator.py"

SHARED_ORM_PERMISSION_PATTERNS = (
    re.compile(r"shared ORM.{0,40}(allowed|permitted|is ok)", re.IGNORECASE),
    re.compile(r"(allow|permit).{0,40}shared (ORM|domain model)", re.IGNORECASE),
    re.compile(r"shared domain models? (are|is) allowed", re.IGNORECASE),
)

FORBIDDEN_PRODUCT_PATTERNS = (
    re.compile(r"smr_form_builder", re.IGNORECASE),
    re.compile(r"\bidurar\b", re.IGNORECASE),
    re.compile(r"objectbox", re.IGNORECASE),
    re.compile(r"dart-define", re.IGNORECASE),
    re.compile(r"smr-wt-", re.IGNORECASE),
)

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@"),
    re.compile(
        r"""(?i)(?:password|secret|api[_-]?key|token)\s*[:=]\s*['"][^'"]{8,}['"]"""
    ),
)

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKER_RE = re.compile(r"HUDHUD_[A-Z0-9]+(?:_[A-Z0-9]+)*")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^## (.+)$")


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


def parse_frontmatter(text: str, path: Path) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing YAML frontmatter delimiter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{path}: incomplete YAML frontmatter")
    raw_meta = parts[1]
    body = parts[2]
    if yaml is None:
        raise RuntimeError("PyYAML is required; install with: uv sync --dev")
    meta = yaml.safe_load(raw_meta)
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter is not a mapping")
    return meta, body


def h2_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        match = HEADING_RE.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = match.group(1).strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def normalize_globs(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def instruction_files() -> list[Path]:
    files = [AGENTS_FILE, CURSOR_DIR / "README.md"]
    files.extend(sorted(RULES_DIR.glob("*.mdc")))
    files.extend(sorted(SKILLS_DIR.glob("*/SKILL.md")))
    return [path for path in files if path.exists()]


def check_agents_md(result: VerificationResult) -> None:
    if not AGENTS_FILE.is_file():
        result.add("agents_md", "Root AGENTS.md is missing", "AGENTS.md")
        return
    text = AGENTS_FILE.read_text(encoding="utf-8")
    if "User changes are never discarded or overwritten." not in text:
        result.add(
            "agents_md",
            "AGENTS.md must state that user changes are never discarded or overwritten",
            "AGENTS.md",
        )
    required_phrases = (
        "No cross-service Python imports",
        "No shared ORM",
        "No cross-service database access",
        "at-least-once",
        "one-writer cutover",
        "Bidirectional dual-write is forbidden",
        "No push",
    )
    lowered = text.lower()
    for phrase in required_phrases:
        if phrase.lower() not in lowered:
            result.add(
                "agents_md",
                f"AGENTS.md missing required policy phrase: {phrase}",
                "AGENTS.md",
            )
    for doc in CANONICAL_DOCS[:3]:
        if doc not in text:
            result.add(
                "canonical_docs",
                f"AGENTS.md does not cite {doc}",
                "AGENTS.md",
            )


def check_rules(result: VerificationResult) -> None:
    if not RULES_DIR.is_dir():
        result.add("rules_exist", "Missing .cursor/rules directory", ".cursor/rules")
        return

    expected = set(ALWAYS_APPLY_RULES) | set(SCOPED_RULES)
    present = {path.name for path in RULES_DIR.glob("*.mdc")}
    for name in sorted(expected - present):
        result.add("rules_exist", f"Required rule file missing: {name}", f".cursor/rules/{name}")

    for path in sorted(RULES_DIR.glob("*.mdc")):
        rel = str(path.relative_to(REPO_ROOT))
        try:
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"), path)
        except (ValueError, RuntimeError) as exc:
            result.add("rule_frontmatter", str(exc), rel)
            continue

        description = meta.get("description")
        if not isinstance(description, str) or not description.strip():
            result.add("rule_frontmatter", "Rule description must be a non-empty string", rel)

        always_apply = meta.get("alwaysApply")
        if not isinstance(always_apply, bool):
            result.add(
                "rule_activation",
                "alwaysApply must be a boolean",
                rel,
            )
            continue

        globs = normalize_globs(meta.get("globs"))
        if path.name in ALWAYS_APPLY_RULES:
            if always_apply is not True:
                result.add(
                    "rule_activation",
                    "Rule must be always-active (alwaysApply: true)",
                    rel,
                )
        elif path.name in SCOPED_RULES:
            if always_apply is not False:
                result.add(
                    "rule_activation",
                    "Scoped rule must not be always-active",
                    rel,
                )
            if not globs:
                result.add(
                    "rule_activation",
                    "Scoped rule must declare globs",
                    rel,
                )
            joined = " ".join(globs).lower()
            for token in SCOPED_RULES[path.name]:
                if token.lower() not in joined:
                    result.add(
                        "rule_activation",
                        f"Scoped globs must include token '{token}'",
                        rel,
                    )
        else:
            result.add(
                "rule_activation",
                f"Unexpected rule file not in the F0.1 catalog: {path.name}",
                rel,
            )

        if len(body.strip()) < REQUIRED_RULE_SECTIONS_MIN_BODY:
            result.add("rule_frontmatter", "Rule body is empty", rel)


def check_skills(result: VerificationResult) -> None:
    if not SKILLS_DIR.is_dir():
        result.add("skills_exist", "Missing .cursor/skills directory", ".cursor/skills")
        return

    seen_markers: dict[str, str] = {}
    for skill_name, expected_marker in SKILL_COMPLETION_MARKERS.items():
        skill_dir = SKILLS_DIR / skill_name
        skill_file = skill_dir / "SKILL.md"
        rel = str(skill_file.relative_to(REPO_ROOT))
        if not skill_file.is_file():
            result.add("skills_exist", f"Missing skill file for {skill_name}", rel)
            continue
        try:
            meta, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"), skill_file)
        except (ValueError, RuntimeError) as exc:
            result.add("skill_frontmatter", str(exc), rel)
            continue

        name = meta.get("name")
        if name != skill_name:
            result.add(
                "skill_frontmatter",
                f"Skill name '{name}' must match directory '{skill_name}'",
                rel,
            )
        if not isinstance(name, str) or not SKILL_NAME_RE.match(name) or len(name) > 64:
            result.add("skill_frontmatter", "Skill name is not Cursor-compatible", rel)

        description = meta.get("description")
        if not isinstance(description, str) or not description.strip():
            result.add("skill_frontmatter", "Skill description must be a non-empty string", rel)
        elif len(description) > 1024:
            result.add("skill_frontmatter", "Skill description exceeds 1024 characters", rel)

        sections = h2_sections(body)
        for heading in REQUIRED_SKILL_SECTIONS:
            content = sections.get(heading, "")
            if heading not in sections:
                result.add("skill_sections", f"Missing required section: {heading}", rel)
            elif not content:
                result.add("skill_sections", f"Empty required section: {heading}", rel)

        marker_section = sections.get("Completion marker", "")
        markers = MARKER_RE.findall(marker_section)
        unique_markers = list(dict.fromkeys(markers))
        if unique_markers != [expected_marker]:
            result.add(
                "skill_markers",
                (
                    f"Completion marker must be exactly {expected_marker}, "
                    f"found {unique_markers}"
                ),
                rel,
            )
        else:
            previous = seen_markers.get(expected_marker)
            if previous:
                result.add(
                    "skill_markers",
                    f"Completion marker {expected_marker} duplicated with {previous}",
                    rel,
                )
            seen_markers[expected_marker] = skill_name

        prohibited = sections.get("Prohibited actions", "")
        for line in REQUIRED_PROHIBITION_LINES:
            if line not in prohibited:
                result.add(
                    "skill_prohibitions",
                    f"Prohibited actions missing required default ban: {line}",
                    rel,
                )
        if re.search(r"(?im)^\s*[-*]\s+push\s*$", prohibited):
            result.add(
                "skill_prohibitions",
                "Unqualified Push allow-list found in prohibited section",
                rel,
            )


def check_shared_orm_and_legacy(result: VerificationResult) -> None:
    for path in instruction_files():
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        for pattern in SHARED_ORM_PERMISSION_PATTERNS:
            if pattern.search(text):
                result.add(
                    "shared_orm",
                    f"Governance file grants shared ORM/domain permission via {pattern.pattern}",
                    rel,
                )

    legacy_rule = RULES_DIR / "01-legacy-read-only.mdc"
    if legacy_rule.is_file():
        text = legacy_rule.read_text(encoding="utf-8")
        if LEGACY_ABSOLUTE_PATH not in text:
            result.add(
                "legacy_read_only",
                "Legacy rule must embed the absolute legacy path",
                str(legacy_rule.relative_to(REPO_ROOT)),
            )
        if LEGACY_DIRTY_FILE not in text:
            result.add(
                "legacy_read_only",
                "Legacy rule must name the pre-existing dirty simulator file",
                str(legacy_rule.relative_to(REPO_ROOT)),
            )
        if not re.search(r"read-only", text, re.IGNORECASE):
            result.add(
                "legacy_read_only",
                "Legacy rule must declare read-only access",
                str(legacy_rule.relative_to(REPO_ROOT)),
            )

    boundaries = REPO_ROOT / "architecture" / "service-boundaries.yaml"
    if boundaries.is_file() and yaml is not None:
        data = yaml.safe_load(boundaries.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            legacy = data.get("legacy_reference", {})
            if not isinstance(legacy, dict) or legacy.get("role") != "read_only_reference":
                result.add(
                    "legacy_read_only",
                    "service-boundaries.yaml legacy_reference.role must be read_only_reference",
                    str(boundaries.relative_to(REPO_ROOT)),
                )
            if isinstance(legacy, dict) and legacy.get("runtime_dependency") != "forbidden":
                result.add(
                    "legacy_read_only",
                    "legacy runtime_dependency must remain forbidden",
                    str(boundaries.relative_to(REPO_ROOT)),
                )


def check_canonical_docs(result: VerificationResult) -> None:
    for rel in CANONICAL_DOCS:
        path = REPO_ROOT / rel
        if not path.is_file():
            result.add("canonical_docs", f"Canonical document missing: {rel}", rel)


def check_product_names_and_secrets(result: VerificationResult) -> None:
    for path in instruction_files():
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PRODUCT_PATTERNS:
            if pattern.search(text):
                result.add(
                    "product_names",
                    f"Unrelated product/repository token matched: {pattern.pattern}",
                    rel,
                )
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                result.add(
                    "secrets",
                    "Secret-like value embedded in a governance file",
                    rel,
                )


def _is_external_link(target: str) -> bool:
    stripped = target.strip()
    return stripped.startswith(("http://", "https://", "mailto:", "#"))


def check_relative_links(result: VerificationResult) -> None:
    for path in instruction_files():
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        for match in MD_LINK_RE.finditer(text):
            target = match.group(2).strip()
            if _is_external_link(target):
                continue
            target_path = target.split("#", 1)[0]
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            try:
                resolved.relative_to(REPO_ROOT.resolve())
            except ValueError:
                result.add(
                    "relative_links",
                    f"Link escapes the repository: {target}",
                    rel,
                )
                continue
            if not resolved.exists():
                result.add(
                    "relative_links",
                    f"Broken relative link: {target}",
                    rel,
                )


def verify() -> VerificationResult:
    result = VerificationResult()
    check_agents_md(result)
    check_rules(result)
    check_skills(result)
    check_shared_orm_and_legacy(result)
    check_canonical_docs(result)
    check_product_names_and_secrets(result)
    check_relative_links(result)
    return result


def main() -> int:
    if yaml is None:
        print("ERROR: PyYAML required. Run: uv sync --dev", file=sys.stderr)
        return 1

    result = verify()
    if result.ok:
        print("Agent governance verification passed.")
        return 0

    print(f"Agent governance verification FAILED ({len(result.violations)} violations):\n")
    for violation in result.violations:
        location = f" [{violation.path}]" if violation.path else ""
        print(f"  [{violation.rule}]{location} {violation.message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
