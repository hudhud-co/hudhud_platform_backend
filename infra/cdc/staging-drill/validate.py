#!/usr/bin/env python3
"""Static validator for CDC staging drill config and evidence manifest."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DRILL_ROOT = Path(__file__).resolve().parent

# Patterns that must not appear in example/config templates committed to git.
SECRET_PATTERNS = (
    re.compile(r"(?i)(password|secret|token|api_key|private_key)\s*=\s*\S+"),
    re.compile(r"(?i)postgresql://[^\s]+"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
)

REQUIRED_CONFIG_KEYS = (
    "CDC_DRILL_DRY_RUN",
    "CDC_DRILL_CONFIRM_DESTRUCTIVE",
    "CDC_DRILL_WAL_LAG_STOP_BYTES",
    "CDC_DRILL_REQUIRE_DURABLE_LANDING_BEFORE_FEEDBACK",
)

REQUIRED_MANIFEST_KEYS = (
    "schema_version",
    "artifact_type",
    "drill_id",
    "hwm_coordination",
    "adr_gates",
)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def check_no_secrets(text: str, label: str) -> list[str]:
    errors: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            errors.append(f"{label}: possible secret or credential material detected")
    return errors


def validate_config(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    errors.extend(check_no_secrets(text, f"config {path.name}"))

    values = parse_env_file(path)

    for key in REQUIRED_CONFIG_KEYS:
        if key not in values:
            errors.append(f"config missing required key: {key}")

    if values.get("CDC_DRILL_DRY_RUN", "1") not in {"0", "1"}:
        errors.append("CDC_DRILL_DRY_RUN must be 0 or 1")

    destructive = values.get("CDC_DRILL_CONFIRM_DESTRUCTIVE", "0")
    if destructive != "0" and path.name == "config.example.env":
        errors.append("config.example.env must keep CDC_DRILL_CONFIRM_DESTRUCTIVE=0")

    if values.get("CDC_DRILL_REQUIRE_DURABLE_LANDING_BEFORE_FEEDBACK", "0") != "1":
        errors.append("CDC_DRILL_REQUIRE_DURABLE_LANDING_BEFORE_FEEDBACK must be 1")

    return errors


def validate_manifest(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    errors.extend(check_no_secrets(text, f"manifest {path.name}"))

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return ["manifest must be a YAML mapping"]

    for key in REQUIRED_MANIFEST_KEYS:
        if key not in data:
            errors.append(f"manifest missing required key: {key}")

    artifact_type = data.get("artifact_type")
    if artifact_type not in {"kit_template", "executed_drill_evidence"}:
        errors.append("artifact_type must be kit_template or executed_drill_evidence")

    hwm = data.get("hwm_coordination", {})
    if isinstance(hwm, dict):
        if hwm.get("ordinary_sql_snapshot_used") is True:
            errors.append("hwm_coordination.ordinary_sql_snapshot_used must not be true for G4")
        overall_pass = data.get("overall", {}).get("pass") is True
        if hwm.get("illustrative_count_only") is True and overall_pass:
            errors.append("cannot pass with illustrative_count_only true")

    gates = data.get("adr_gates", {})
    g4_not_pass = gates.get("G4_export_snapshot_drill") != "pass"
    if isinstance(gates, dict) and artifact_type == "executed_drill_evidence" and g4_not_pass:
        errors.append(
            "executed evidence requires G4_export_snapshot_drill: pass for zero-gap claim"
        )

    return errors


def validate_preflight_sql(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    forbidden = re.compile(
        r"(?im)^\s*(CREATE|DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE|GRANT|REVOKE)\s+"
    )
    for match in forbidden.finditer(text):
        snippet = match.group(0).strip()
        if snippet.upper().startswith("CREATE_REPLICATION_SLOT"):
            continue  # appears only in comments/templates elsewhere
        errors.append(f"preflight SQL must be read-only; found: {snippet}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate CDC staging drill kit artifacts.")
    parser.add_argument("--config", type=Path, default=DRILL_ROOT / "config.example.env")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DRILL_ROOT / "templates" / "evidence-manifest.yaml",
    )
    parser.add_argument(
        "--preflight-sql",
        type=Path,
        default=DRILL_ROOT / "sql" / "preflight-readonly.sql",
    )
    args = parser.parse_args(argv)

    errors: list[str] = []
    if args.config.is_file():
        errors.extend(validate_config(args.config))
    else:
        errors.append(f"config not found: {args.config}")

    if args.manifest.is_file():
        errors.extend(validate_manifest(args.manifest))
    else:
        errors.append(f"manifest not found: {args.manifest}")

    if args.preflight_sql.is_file():
        errors.extend(validate_preflight_sql(args.preflight_sql))
    else:
        errors.append(f"preflight SQL not found: {args.preflight_sql}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("CDC staging drill kit validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
