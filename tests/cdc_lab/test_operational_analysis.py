"""Static checks that CDC operational analysis covers required ADR-0007 topics."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OPERATIONS_DOC = REPO_ROOT / "infra" / "labs" / "legacy-cdc" / "OPERATIONS.md"

REQUIRED_TOPICS = (
    "Privileges",
    "Replication-slot lifecycle",
    "WAL disk risk",
    "Failover limitations",
    "Monitoring signals",
    "Snapshot coordination",
    "Schema evolution",
    "PII exposure",
    "Delete / tombstone behavior",
    "Bridge persistence before acknowledging progress",
    "Transport completeness",
    "domain event",
)


def test_operations_doc_exists() -> None:
    assert OPERATIONS_DOC.is_file()


def test_operations_doc_covers_required_topics() -> None:
    text = OPERATIONS_DOC.read_text(encoding="utf-8").lower()
    for topic in REQUIRED_TOPICS:
        assert topic.lower() in text, f"missing operational topic: {topic}"


def test_operations_doc_states_single_node_not_ha() -> None:
    text = OPERATIONS_DOC.read_text(encoding="utf-8")
    assert "single-node" in text.lower() or "single node" in text.lower()
    assert "not" in text.lower() and "ha" in text.lower()
