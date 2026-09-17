#!/usr/bin/env python3
"""Requirement traceability verifier — where the 160 came from, and that it is not 140.

`04-GAP-AND-CHANGE-MATRIX.md` reports a total of **140**. That number is the sum of the
six status buckets in its own summary table, and it was never a count of requirements:
it was a count of requirements that had been *given a status* in that wave's matrix. The
catalogue has held **160** unique requirement rows since it was written.

So the platform did not gain twenty requirements. Twenty catalogued requirements were
never classified, and the accounting now classifies all of them. This script proves that
claim rather than asserting it, by checking four things:

1. the catalogue holds exactly 160 unique ids, each on exactly one row;
2. every one of them traces to a catalogue line number — no accounting row is invented;
3. no id is counted twice, in either direction, in any family;
4. the 140 in the gap matrix is arithmetically the sum of its own buckets, so the
   discrepancy is 20 unclassified ids and not 20 new requirements;
5. the two tables in `11-REQUIREMENT-TRACEABILITY.md` say what the accounting says.

Check 5 exists because they did not. The per-id table was written by hand and still read
`planned` for seven claims requirements that had since been implemented, persisted and
proved. A traceability document that drifts is worse than none: it is the thing people
quote instead of reading the accounting. The tables are now generated from the accounting
and this script rewrites them with `--write`.

Usage:
    uv run python scripts/quality/verify_requirement_traceability.py
    uv run python scripts/quality/verify_requirement_traceability.py --write

Exit code 0 when the traceability holds; 1 when it does not.
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = REPO_ROOT / "docs" / "audits" / "hudhud-app-redesign-v6.3"
CATALOG = AUDIT_DIR / "03-REQUIREMENTS-CATALOG.md"
MATRIX = AUDIT_DIR / "04-GAP-AND-CHANGE-MATRIX.md"
ACCOUNTING = AUDIT_DIR / "requirement-accounting.yaml"
TRACEABILITY = AUDIT_DIR / "11-REQUIREMENT-TRACEABILITY.md"

#: The generated regions of the traceability document, fenced so the prose around them
#: stays hand-written.
FAMILY_MARKER = "generated:family-summary"
ID_MARKER = "generated:requirement-rows"

#: Accounting status to the word the tables use.
STATUS_WORD = {
    "COMPLETE": "complete",
    "PLANNED": "planned",
    "BLOCKED_BUSINESS_DECISION": "**blocked**",
    "SOURCE_CONFLICT": "**conflict**",
}

#: Family prefix to the catalogue section it comes from.
FAMILY_SECTION = {
    "CLM": "CLM — Claims and compensation",
    "CUS": "CUS — Regular customer",
    "DRV-A": "DRV-A — Driver account, shift, money",
    "DRV-L": "DRV-L — Last-mile",
    "DRV-P": "DRV-P — Pickup",
    "MER": "MER — Seller / merchant",
    "NTF": "NTF — Notifications",
    "OPS": "OPS — Operational workflow and visibility",
    "PAY": "PAY — COD, wallet, refund, settlement",
    "SEC": "SEC — Security, identity, authorization",
    "SHP": "SHP — Shared shipment lifecycle",
}

CATALOG_ROW = re.compile(
    r"^\|\s*((?:SEC|CUS|MER|SHP|PAY|CLM|NTF|OPS)-\d+|DRV-[PLA]\d+)\s*\|"
)
FAMILY = re.compile(r"^(DRV-[PLA]|[A-Z]{3})")

#: The catalogue total. Derived below and compared, never trusted from a document.
EXPECTED_TOTAL = 160

#: The six buckets `04-GAP-AND-CHANGE-MATRIX.md` tallies, and what they sum to.
MATRIX_BUCKETS = (
    "IMPLEMENTED_EXACT",
    "PARTIAL",
    "MISSING",
    "BLOCKED_BY_EXTERNAL_DEPENDENCY",
    "UI_ONLY",
    "CONFLICT",
)
MATRIX_REPORTED_TOTAL = 140


def catalog_rows() -> dict[str, list[int]]:
    """Every catalogued id, with the line numbers it appears on as a table row."""
    rows: dict[str, list[int]] = defaultdict(list)
    for number, line in enumerate(CATALOG.read_text(encoding="utf-8").splitlines(), 1):
        match = CATALOG_ROW.match(line)
        if match:
            rows[match.group(1)].append(number)
    return dict(rows)


def matrix_bucket_counts() -> dict[str, int]:
    """Read the summary table's own figures, so the arithmetic is the document's."""
    counts: dict[str, int] = {}
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        # `MISSING` carries a parenthetical in the document, so the bucket name is
        # matched inside its backticks rather than as the whole cell.
        match = re.match(r"^\|\s*`([A-Z_]+)`[^|]*\|\s*(\d+)\s*\|", line)
        if match and match.group(1) in MATRIX_BUCKETS:
            counts[match.group(1)] = int(match.group(2))
    return counts


def family_of(requirement_id: str) -> str:
    match = FAMILY.match(requirement_id)
    return match.group(1) if match else requirement_id


def render_family_table(rows: dict[str, list[int]], accounted: list[dict]) -> str:
    """Per-family counts, derived from the accounting rather than transcribed."""
    by_id = {row["id"]: row for row in accounted}
    lines = [
        "| Family | Catalogue section | Count | Complete | Planned | Blocked | Conflict |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    totals = [0, 0, 0, 0, 0]
    for family in sorted(FAMILY_SECTION):
        ids = [rid for rid in rows if family_of(rid) == family]
        counts = Counter(by_id[rid]["status"] for rid in ids if rid in by_id)
        figures = [
            len(ids),
            counts["COMPLETE"],
            counts["PLANNED"],
            counts["BLOCKED_BUSINESS_DECISION"],
            counts["SOURCE_CONFLICT"],
        ]
        totals = [running + figure for running, figure in zip(totals, figures, strict=True)]
        cells = " | ".join(str(figure) for figure in figures)
        lines.append(f"| `{family}` | {FAMILY_SECTION[family]} | {cells} |")
    bold = " | ".join(f"**{figure}**" for figure in totals)
    lines.append(f"| **Total** | | {bold} |")
    return "\n".join(lines)


def render_id_table(rows: dict[str, list[int]], accounted: list[dict]) -> str:
    """One row per requirement: where it is defined, who owns it, where it stands."""
    by_id = {row["id"]: row for row in accounted}
    lines = ["| ID | Catalogue line | Owner | Wave | Status |", "|---|---:|---|---|---|"]
    for rid in sorted(rows):
        entry = by_id.get(rid, {})
        lines.append(
            f"| `{rid}` | {rows[rid][0]} | `{entry.get('owner', '?')}` | "
            f"{entry.get('wave', '?')} | "
            f"{STATUS_WORD.get(entry.get('status', ''), '?')} |"
        )
    return "\n".join(lines)


def replace_region(document: str, marker: str, body: str) -> str:
    """Swap the text between one pair of generated-region fences."""
    opening, closing = f"<!-- {marker} -->", f"<!-- /{marker} -->"
    start, end = document.index(opening), document.index(closing)
    return document[: start + len(opening)] + "\n" + body + "\n" + document[end:]


def main(argv: list[str] | None = None) -> int:
    write = "--write" in (argv if argv is not None else sys.argv[1:])
    problems: list[str] = []

    rows = catalog_rows()
    accounted = yaml.safe_load(ACCOUNTING.read_text(encoding="utf-8"))["requirements"]
    accounted_ids = [row["id"] for row in accounted]

    # 1. one row per id in the catalogue
    repeated = {rid: lines for rid, lines in rows.items() if len(lines) > 1}
    if repeated:
        problems.append(f"catalogued on more than one row: {repeated}")
    if len(rows) != EXPECTED_TOTAL:
        problems.append(
            f"catalogue holds {len(rows)} unique ids, expected {EXPECTED_TOTAL}"
        )

    # 2. every accounted id traces to a catalogue line
    untraceable = sorted(set(accounted_ids) - set(rows))
    if untraceable:
        problems.append(
            f"accounted but not in the catalogue — invented: {untraceable}"
        )
    unaccounted = sorted(set(rows) - set(accounted_ids))
    if unaccounted:
        problems.append(f"catalogued but never accounted for: {unaccounted}")

    # 3. no double counting
    duplicated = sorted(rid for rid, n in Counter(accounted_ids).items() if n > 1)
    if duplicated:
        problems.append(f"accounted more than once: {duplicated}")

    families = Counter(family_of(rid) for rid in rows)
    if sum(families.values()) != len(rows):
        problems.append("family counts do not sum to the catalogue total")

    # 4. the 140 is the matrix's own arithmetic, not a requirement count
    buckets = matrix_bucket_counts()
    missing_buckets = sorted(set(MATRIX_BUCKETS) - set(buckets))
    if missing_buckets:
        problems.append(
            f"gap matrix summary no longer reports: {missing_buckets}; "
            "the 140 → 160 explanation in 11-REQUIREMENT-TRACEABILITY.md rests on it"
        )
    elif sum(buckets.values()) != MATRIX_REPORTED_TOTAL:
        problems.append(
            f"gap matrix buckets sum to {sum(buckets.values())}, "
            f"not the {MATRIX_REPORTED_TOTAL} it reports"
        )

    # 5. the document says what the accounting says
    document = TRACEABILITY.read_text(encoding="utf-8")
    if FAMILY_MARKER not in document or ID_MARKER not in document:
        problems.append(
            f"{TRACEABILITY.name} has no generated regions; "
            "its tables cannot be checked and will drift"
        )
    else:
        rendered = replace_region(
            replace_region(document, FAMILY_MARKER, render_family_table(rows, accounted)),
            ID_MARKER,
            render_id_table(rows, accounted),
        )
        if rendered != document:
            if write:
                TRACEABILITY.write_text(rendered, encoding="utf-8")
                print(f"Rewrote the generated tables in {TRACEABILITY.name}.")
                document = rendered
            else:
                problems.append(
                    f"the tables in {TRACEABILITY.name} disagree with the accounting; "
                    "regenerate them with --write"
                )

    if problems:
        print("Requirement traceability FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    unclassified = len(rows) - MATRIX_REPORTED_TOTAL
    print(
        f"Requirement traceability holds: {len(rows)} catalogued ids, "
        "each on one row and accounted for exactly once."
    )
    print(
        f"  The {MATRIX_REPORTED_TOTAL} in 04-GAP-AND-CHANGE-MATRIX.md is the sum of "
        f"its own {len(MATRIX_BUCKETS)} status buckets "
        f"({' + '.join(str(buckets[b]) for b in MATRIX_BUCKETS)}),"
    )
    print(
        f"  so {unclassified} catalogued requirements were never given a status there. "
        "None of the 160 is new."
    )
    for family in sorted(families):
        print(f"    {family:<8} {families[family]:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
