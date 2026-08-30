"""Static validation for ADR-0002 JetStream topology definitions."""

from __future__ import annotations

from .conftest import EVENTING_ROOT, load_topology_yaml

REQUIRED_STREAMS = {
    "HUDHUD_SHIPMENT",
    "HUDHUD_PICKUP",
    "HUDHUD_HUB",
    "HUDHUD_LINEHAUL",
    "HUDHUD_DELIVERY",
    "HUDHUD_WALLET",
    "HUDHUD_NOTIFICATION",
    "HUDHUD_AUDIT",
    "HUDHUD_DLQ",
}


def test_streams_manifest_lists_all_context_streams() -> None:
    doc = load_topology_yaml("streams.yaml")
    names = {entry["name"] for entry in doc["streams"]}
    assert names == REQUIRED_STREAMS


def test_all_streams_use_file_storage_and_single_replica() -> None:
    doc = load_topology_yaml("streams.yaml")
    defaults = doc["defaults"]
    assert defaults["storage"] == "file"
    assert defaults["num_replicas"] == 1
    assert defaults["retention"] == "limits"


def test_stream_subjects_use_hudhud_prefix_glob() -> None:
    doc = load_topology_yaml("streams.yaml")
    for entry in doc["streams"]:
        for subject in entry["subjects"]:
            assert subject.startswith("hudhud.")
            assert subject.endswith(".>")


def test_audit_stream_documents_transport_only_role() -> None:
    doc = load_topology_yaml("streams.yaml")
    audit = next(item for item in doc["streams"] if item["name"] == "HUDHUD_AUDIT")
    assert "not" in audit["description"].lower() or "transport" in audit["description"].lower()


def test_consumers_use_distinct_durables_per_projection() -> None:
    doc = load_topology_yaml("consumers.yaml")
    durables = [entry["durable_name"] for entry in doc["consumers"]]
    assert len(durables) == len(set(durables))


def test_no_global_stream_durable_without_filter() -> None:
    doc = load_topology_yaml("consumers.yaml")
    for entry in doc["consumers"]:
        assert entry.get("filter_subject"), entry["durable_name"]
        assert entry["filter_subject"] != ">"


def test_shipment_stream_has_multiple_independent_durables() -> None:
    doc = load_topology_yaml("consumers.yaml")
    shipment_durables = [
        entry["durable_name"]
        for entry in doc["consumers"]
        if entry["stream"] == "HUDHUD_SHIPMENT"
    ]
    assert len(shipment_durables) >= 2
    filters = [
        entry["filter_subject"]
        for entry in doc["consumers"]
        if entry["stream"] == "HUDHUD_SHIPMENT"
    ]
    assert len(set(filters)) == len(filters)


def test_consumer_defaults_match_adr_provisional_values() -> None:
    doc = load_topology_yaml("consumers.yaml")
    defaults = doc["defaults"]
    assert defaults["ack_policy"] == "explicit"
    assert defaults["ack_wait"] == "30s"
    assert defaults["max_deliver"] == 5
    assert defaults["duplicate_window"] == "2m"
    assert defaults["backoff"] == ["5s", "30s", "2m", "10m", "30m"]


def test_no_production_secrets_in_eventing_tree() -> None:
    forbidden_tokens = (
        "AKIA",
        "password:",
        "Bearer ",
        "postgresql://",
        "-----BEGIN",
    )
    for path in EVENTING_ROOT.rglob("*"):
        if path.is_dir() or path.suffix not in {".yaml", ".yml", ".sh", ".py", ".md", ".example"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.name == "entrypoint.sh" and "dev-eventing-local-only" in text:
            continue
        for token in forbidden_tokens:
            assert token not in text, (path, token)


def test_bootstrap_script_exists_and_is_idempotent_by_design() -> None:
    script = EVENTING_ROOT / "scripts" / "bootstrap_topology.py"
    source = script.read_text(encoding="utf-8")
    assert "ensure_stream" in source
    assert "ensure_consumer" in source
    assert "NotFoundError" in source


def test_runbook_states_not_ha_and_not_audit_store() -> None:
    runbook = (EVENTING_ROOT / "runbook.md").read_text(encoding="utf-8")
    assert "Not HA" in runbook or "not HA" in runbook
    assert "not legal" in runbook.lower() or "Not legal" in runbook
