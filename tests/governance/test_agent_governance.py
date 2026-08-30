"""Structured tests for HUDHUD Cursor agent governance."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "quality" / "verify_agent_governance.py"
ARCHITECTURE_TESTS = REPO_ROOT / "tests" / "architecture" / "test_boundaries.py"
RULES_DIR = REPO_ROOT / ".cursor" / "rules"
SKILLS_DIR = REPO_ROOT / ".cursor" / "skills"


def load_governance_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_agent_governance", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gov() -> ModuleType:
    return load_governance_module()


def test_governance_verifier_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Agent governance verification passed." in result.stdout


def test_agents_md_exists_and_states_user_preservation() -> None:
    agents = REPO_ROOT / "AGENTS.md"
    assert agents.is_file()
    text = agents.read_text(encoding="utf-8")
    assert "User changes are never discarded or overwritten." in text
    assert "hudhud_platform_backend" in text


def test_required_rule_files_and_activation_modes(gov: ModuleType) -> None:
    always = set(gov.ALWAYS_APPLY_RULES)
    scoped = set(gov.SCOPED_RULES)
    present = {path.name for path in RULES_DIR.glob("*.mdc")}
    assert always <= present
    assert scoped <= present
    assert always.isdisjoint(scoped)

    for name in always:
        path = RULES_DIR / name
        meta, _body = gov.parse_frontmatter(path.read_text(encoding="utf-8"), path)
        assert meta["alwaysApply"] is True
        assert isinstance(meta["description"], str) and meta["description"].strip()

    for name, tokens in gov.SCOPED_RULES.items():
        path = RULES_DIR / name
        meta, _body = gov.parse_frontmatter(path.read_text(encoding="utf-8"), path)
        assert meta["alwaysApply"] is False
        globs = gov.normalize_globs(meta.get("globs"))
        assert globs, name
        joined = " ".join(globs).lower()
        for token in tokens:
            assert token.lower() in joined, (name, token, globs)


def test_required_skills_sections_and_unique_markers(gov: ModuleType) -> None:
    markers: list[str] = []
    for skill_name, expected in gov.SKILL_COMPLETION_MARKERS.items():
        path = SKILLS_DIR / skill_name / "SKILL.md"
        assert path.is_file(), skill_name
        meta, body = gov.parse_frontmatter(path.read_text(encoding="utf-8"), path)
        assert meta["name"] == skill_name
        sections = gov.h2_sections(body)
        for heading in gov.REQUIRED_SKILL_SECTIONS:
            assert heading in sections, (skill_name, heading)
            assert sections[heading].strip(), (skill_name, heading)
        found = gov.MARKER_RE.findall(sections["Completion marker"])
        assert found == [expected], (skill_name, found)
        markers.append(expected)
    assert len(markers) == len(set(markers))


def test_skills_do_not_authorize_push_or_destructive_git_by_default(gov: ModuleType) -> None:
    for skill_name in gov.SKILL_COMPLETION_MARKERS:
        path = SKILLS_DIR / skill_name / "SKILL.md"
        _meta, body = gov.parse_frontmatter(path.read_text(encoding="utf-8"), path)
        prohibited = gov.h2_sections(body)["Prohibited actions"]
        for required in gov.REQUIRED_PROHIBITION_LINES:
            assert required in prohibited, (skill_name, required)


def test_governance_does_not_grant_shared_orm_or_domain_models(gov: ModuleType) -> None:
    for path in gov.instruction_files():
        text = path.read_text(encoding="utf-8")
        for pattern in gov.SHARED_ORM_PERMISSION_PATTERNS:
            assert pattern.search(text) is None, path


def test_legacy_remains_explicitly_read_only(gov: ModuleType) -> None:
    rule = RULES_DIR / "01-legacy-read-only.mdc"
    text = rule.read_text(encoding="utf-8")
    assert gov.LEGACY_ABSOLUTE_PATH in text
    assert gov.LEGACY_DIRTY_FILE in text
    boundaries = yaml.safe_load(
        (REPO_ROOT / "architecture" / "service-boundaries.yaml").read_text(encoding="utf-8")
    )
    legacy = boundaries["legacy_reference"]
    assert legacy["role"] == "read_only_reference"
    assert legacy["runtime_dependency"] == "forbidden"
    assert legacy["path"] == gov.LEGACY_ABSOLUTE_PATH


def test_canonical_architecture_documents_exist(gov: ModuleType) -> None:
    for rel in gov.CANONICAL_DOCS:
        assert (REPO_ROOT / rel).is_file(), rel


def test_no_unrelated_product_names_or_secret_values(gov: ModuleType) -> None:
    for path in gov.instruction_files():
        text = path.read_text(encoding="utf-8")
        for pattern in gov.FORBIDDEN_PRODUCT_PATTERNS:
            assert pattern.search(text) is None, (path, pattern.pattern)
        for pattern in gov.SECRET_PATTERNS:
            assert pattern.search(text) is None, path


def test_relative_markdown_links_resolve(gov: ModuleType) -> None:
    result = gov.VerificationResult()
    gov.check_relative_links(result)
    assert result.ok, result.violations


def test_existing_architecture_tests_not_removed() -> None:
    source = ARCHITECTURE_TESTS.read_text(encoding="utf-8")
    for name in (
        "test_verify_boundaries_script_passes",
        "test_shipment_sole_lifecycle_writer_invariant",
        "test_gateway_does_not_own_domain_tables",
        "test_legacy_reference_is_read_only",
        "test_no_root_alembic_migrations",
    ):
        assert f"def {name}" in source


def test_cursor_readme_distinguishes_rules_and_skills() -> None:
    readme = (REPO_ROOT / ".cursor" / "README.md").read_text(encoding="utf-8")
    assert "Rules vs Skills" in readme
    assert "legacy-evidence-audit" in readme
    assert "parallel-worktree-wave" in readme
    assert "HUDHUD" in readme
