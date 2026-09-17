#!/usr/bin/env python3
"""Requirement accounting verifier.

Guards the one property the audit pack must never lose: every requirement in
`03-REQUIREMENTS-CATALOG.md` appears in `requirement-accounting.yaml` exactly once,
with a status it can actually support.

A `COMPLETE` row must name implementation evidence that exists on disk. A blocked row
must name the business decision it waits on. A conflicted row must record both
alternatives rather than silently picking one.

Usage:
    uv run python scripts/quality/verify_requirement_accounting.py

Exit code 0 when the accounting reconciles; 1 when it does not.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = REPO_ROOT / "docs" / "audits" / "hudhud-app-redesign-v6.3"
CATALOG = AUDIT_DIR / "03-REQUIREMENTS-CATALOG.md"
ACCOUNTING = AUDIT_DIR / "requirement-accounting.yaml"
SERVICES_DIR = REPO_ROOT / "services"

CATALOG_ROW = re.compile(
    r"^\|\s*((?:SEC|CUS|MER|SHP|PAY|CLM|NTF|OPS)-\d+|DRV-[PLA]\d+)\s*\|"
)

VALID_STATUSES = {
    "COMPLETE",
    "PLANNED",
    "BLOCKED_BUSINESS_DECISION",
    "SOURCE_CONFLICT",
}

# The only two reasons a requirement may remain unresolved at the end of the mission.
# Both are fixed lists: growing either one is a governance decision, not a silent edit.
EXECUTIVE_OPEN_ITEMS = {"MER-02", "PAY-07", "CLM-08", "SHP-02", "PAY-08"}
SOURCE_CONFLICTS = {"DRV-L05", "DRV-L07"}


def catalog_ids() -> list[str]:
    ids: list[str] = []
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        match = CATALOG_ROW.match(line)
        if match:
            ids.append(match.group(1))
    return ids


def main() -> int:
    problems: list[str] = []

    for path in (CATALOG, ACCOUNTING):
        if not path.is_file():
            print(f"missing required file: {path.relative_to(REPO_ROOT)}")
            return 1

    ids = catalog_ids()
    duplicated_in_catalog = [rid for rid, n in Counter(ids).items() if n > 1]
    if duplicated_in_catalog:
        problems.append(f"catalog lists ids more than once: {sorted(duplicated_in_catalog)}")

    document = yaml.safe_load(ACCOUNTING.read_text(encoding="utf-8")) or {}
    rows = document.get("requirements") or []
    if not isinstance(rows, list):
        print("requirement-accounting.yaml: 'requirements' must be a list")
        return 1

    seen: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    known_services = {path.name for path in SERVICES_DIR.iterdir() if path.is_dir()}

    for row in rows:
        rid = str(row.get("id", "<missing id>"))
        seen[rid] += 1
        status = str(row.get("status", ""))
        by_status[status] += 1

        if status not in VALID_STATUSES:
            problems.append(f"{rid}: unknown status {status!r}")

        owner = str(row.get("owner", ""))
        if owner not in known_services:
            problems.append(f"{rid}: owner {owner!r} is not a service under services/")

        if not str(row.get("title", "")).strip():
            problems.append(f"{rid}: missing title")
        if not str(row.get("product_evidence", "")).strip():
            problems.append(f"{rid}: missing product_evidence citation")

        if status == "COMPLETE":
            evidence = row.get("implementation_evidence") or []
            if not evidence:
                problems.append(f"{rid}: COMPLETE without implementation_evidence")
            for item in evidence:
                if not (REPO_ROOT / str(item)).exists():
                    problems.append(f"{rid}: implementation_evidence path missing: {item}")

        if status == "BLOCKED_BUSINESS_DECISION":
            if rid not in EXECUTIVE_OPEN_ITEMS:
                problems.append(
                    f"{rid}: BLOCKED_BUSINESS_DECISION is reserved for the v6.3 "
                    f"Appendix A open items {sorted(EXECUTIVE_OPEN_ITEMS)}"
                )
            for field in ("decision_source", "why_blocked"):
                if not str(row.get(field, "")).strip():
                    problems.append(f"{rid}: blocked row missing {field}")

        if status == "SOURCE_CONFLICT":
            if rid not in SOURCE_CONFLICTS:
                problems.append(
                    f"{rid}: SOURCE_CONFLICT is reserved for the documented "
                    f"contradictions {sorted(SOURCE_CONFLICTS)}"
                )
            alternatives = row.get("alternatives") or []
            if len(alternatives) < 2:
                problems.append(f"{rid}: conflict row must record both alternatives")
            if not str(row.get("why_blocked", "")).strip():
                problems.append(f"{rid}: conflict row missing why_blocked")

    repeated = sorted(rid for rid, n in seen.items() if n > 1)
    if repeated:
        problems.append(f"accounted more than once: {repeated}")

    catalog_set, accounted_set = set(ids), set(seen)
    unaccounted = sorted(catalog_set - accounted_set)
    if unaccounted:
        problems.append(f"catalogued but not accounted: {unaccounted}")
    invented = sorted(accounted_set - catalog_set)
    if invented:
        problems.append(f"accounted but not in the catalog: {invented}")

    declared_total = document.get("total_requirements")
    if declared_total != len(ids):
        problems.append(
            f"total_requirements says {declared_total}, catalog has {len(ids)}"
        )

    for rid in sorted(EXECUTIVE_OPEN_ITEMS):
        row = next((r for r in rows if r.get("id") == rid), None)
        if row is not None and row.get("status") != "BLOCKED_BUSINESS_DECISION":
            problems.append(
                f"{rid} is a v6.3 executive open item but is marked "
                f"{row.get('status')!r} — the decision has not been made"
            )
    for rid in sorted(SOURCE_CONFLICTS):
        row = next((r for r in rows if r.get("id") == rid), None)
        if row is not None and row.get("status") != "SOURCE_CONFLICT":
            problems.append(
                f"{rid} is a documented source contradiction but is marked "
                f"{row.get('status')!r}"
            )

    if problems:
        print("Requirement accounting verification FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"Requirement accounting reconciles: {len(ids)} requirements, each exactly once.")
    for status in sorted(by_status):
        print(f"  {status:26} {by_status[status]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
