"""Static tests for the CDC staging drill kit (ADR-0007 W4-C)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DRILL_ROOT = REPO_ROOT / "infra" / "cdc" / "staging-drill"

REQUIRED_ARTIFACTS = (
    "README.md",
    "RUNBOOK.md",
    "config.example.env",
    "sql/preflight-readonly.sql",
    "templates/allowlist-inventory.yaml",
    "templates/evidence-manifest.yaml",
    "checklists/reconciliation.md",
    "checklists/stop-conditions.md",
    "procedures/cleanup-slot-retirement.md",
    "commands/README.md",
    "commands/readonly.sh",
    "commands/privileged-manual.sh",
    "commands/destructive-cleanup.sh",
    "validate.py",
)

RUNBOOK_REQUIRED_TOPICS = (
    "EXPORT_SNAPSHOT",
    "illustrative",
    "durable landing",
    "preflight failure",
    "replication protocol",
    "zero-gap",
    "G4",
    "G5",
    "G8",
    "destructive",
    "dry-run",
)

SECRET_PATTERNS = (
    re.compile(r"(?i)(password|secret|token)\s*=\s*[^\s#]+", re.MULTILINE),
    re.compile(r"postgresql://"),
    re.compile(r"-----BEGIN PRIVATE KEY-----"),
)


@pytest.mark.parametrize("relative_path", REQUIRED_ARTIFACTS)
def test_required_artifact_exists(relative_path: str) -> None:
    path = DRILL_ROOT / relative_path
    assert path.is_file(), f"missing artifact: {relative_path}"


def test_runbook_covers_required_topics() -> None:
    text = (DRILL_ROOT / "RUNBOOK.md").read_text(encoding="utf-8").lower()
    for topic in RUNBOOK_REQUIRED_TOPICS:
        assert topic.lower() in text, f"RUNBOOK missing topic: {topic}"


def test_runbook_labels_kit_not_executed_evidence() -> None:
    text = (DRILL_ROOT / "RUNBOOK.md").read_text(encoding="utf-8").lower()
    assert "kit" in text
    assert "not" in text and "executed" in text


def test_config_example_has_safe_defaults() -> None:
    config = (DRILL_ROOT / "config.example.env").read_text(encoding="utf-8")
    assert "CDC_DRILL_DRY_RUN=1" in config
    assert "CDC_DRILL_CONFIRM_DESTRUCTIVE=0" in config
    assert "CDC_DRILL_REQUIRE_DURABLE_LANDING_BEFORE_FEEDBACK=1" in config


def test_examples_contain_no_secrets() -> None:
    paths = [
        DRILL_ROOT / "config.example.env",
        DRILL_ROOT / "templates" / "allowlist-inventory.yaml",
        DRILL_ROOT / "templates" / "evidence-manifest.yaml",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), f"possible secret in {path.name}"


def test_preflight_sql_is_read_only() -> None:
    sql = (DRILL_ROOT / "sql" / "preflight-readonly.sql").read_text(encoding="utf-8")
    forbidden = re.compile(
        r"(?im)^\s*(DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE|GRANT|REVOKE)\s+"
    )
    assert not forbidden.search(sql), "preflight SQL contains mutating statements"
    assert "CREATE_REPLICATION_SLOT" not in sql.upper().replace("--", "")


def test_destructive_script_blocked_by_default() -> None:
    env = {"CDC_DRILL_CONFIG": str(DRILL_ROOT / "config.example.env")}
    result = subprocess.run(
        ["sh", str(DRILL_ROOT / "commands" / "destructive-cleanup.sh"), "drop-slot"],
        cwd=REPO_ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "BLOCKED" in result.stdout + result.stderr


def test_destructive_script_requires_exact_slot_name() -> None:
    env = {
        "CDC_DRILL_CONFIG": str(DRILL_ROOT / "config.example.env"),
        "CDC_DRILL_CONFIRM_DESTRUCTIVE": "1",
        "CDC_DRILL_SLOT_NAME": "",
    }
    result = subprocess.run(
        ["sh", str(DRILL_ROOT / "commands" / "destructive-cleanup.sh"), "drop-slot"],
        cwd=REPO_ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "CDC_DRILL_SLOT_NAME" in result.stdout + result.stderr


def test_destructive_script_rejects_wildcard_slot() -> None:
    env = {
        "CDC_DRILL_CONFIRM_DESTRUCTIVE": "1",
        "CDC_DRILL_SLOT_NAME": "hudhud_bridge_staging_*",
    }
    result = subprocess.run(
        ["sh", str(DRILL_ROOT / "commands" / "destructive-cleanup.sh"), "drop-slot"],
        cwd=REPO_ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "wildcard" in (result.stdout + result.stderr).lower()


def test_readonly_script_defaults_to_dry_run() -> None:
    result = subprocess.run(
        ["sh", str(DRILL_ROOT / "commands" / "readonly.sh"), "preflight"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "DRY-RUN" in result.stdout


def test_validator_passes_on_kit_templates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(DRILL_ROOT / "validate.py"),
            "--config",
            str(DRILL_ROOT / "config.example.env"),
            "--manifest",
            str(DRILL_ROOT / "templates" / "evidence-manifest.yaml"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout.lower()


def test_evidence_manifest_template_is_kit_type() -> None:
    data = yaml.safe_load(
        (DRILL_ROOT / "templates" / "evidence-manifest.yaml").read_text(encoding="utf-8")
    )
    assert data["artifact_type"] == "kit_template"


def test_allowlist_includes_shipment_events_and_audit_logs() -> None:
    data = yaml.safe_load(
        (DRILL_ROOT / "templates" / "allowlist-inventory.yaml").read_text(encoding="utf-8")
    )
    tables = {s["table"] for s in data["sources"]}
    assert "public.shipment_events" in tables
    assert "public.audit_logs" in tables
