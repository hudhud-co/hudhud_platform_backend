"""Static validation for ADR-0002 JetStream topology definitions."""

from __future__ import annotations

from .conftest import EVENTING_ROOT, load_topology_yaml

REQUIRED_STREAMS = {
    "HUDHUD_SHIPMENT",
    "HUDHUD_PICKUP",
    "HUDHUD_HUB",
    "HUDHUD_LINEHAUL",
    "HUDHUD_DELIVERY",
    "HUDHUD_FINANCE",
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


def test_shipment_pickup_facts_durable_uses_exact_accepted_subject() -> None:
    doc = load_topology_yaml("consumers.yaml")
    entry = next(
        item for item in doc["consumers"] if item["durable_name"] == "shipment_pickup_facts_v1"
    )
    assert entry["stream"] == "HUDHUD_PICKUP"
    assert entry["filter_subject"] == "hudhud.pickup.pickup.fact.accepted.v1"
    assert doc["defaults"]["ack_policy"] == "explicit"
    durables = [
        item["durable_name"]
        for item in doc["consumers"]
        if item["stream"] == "HUDHUD_PICKUP"
    ]
    assert durables == ["shipment_pickup_facts_v1"]


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


def test_jetstream_max_msg_size_coherent_with_envelope_hard_limit() -> None:
    doc = load_topology_yaml("streams.yaml")
    max_msg_size = int(doc["defaults"]["max_msg_size_bytes"])
    provisional_envelope_hard_limit = 256 * 1024
    assert max_msg_size >= provisional_envelope_hard_limit


def test_subject_grammar_document_exists() -> None:
    grammar = EVENTING_ROOT / "subject-grammar.md"
    text = grammar.read_text(encoding="utf-8")
    assert "hudhud.{producer}.{event_type}.v{event_version}" in text
    assert "provisional" in text.lower()


ACCEPTED_BRIDGE_OBSERVATION_SUBJECTS = {
    "hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1": "HUDHUD_SHIPMENT",
    "hudhud.audit.legacy_bridge.observation.audit_entry.v1": "HUDHUD_AUDIT",
}


def test_non_aggregate_subject_grammar_is_documented() -> None:
    grammar = (EVENTING_ROOT / "subject-grammar.md").read_text(encoding="utf-8")
    assert (
        "hudhud.{domain_context}.{producer}.{semantic_class}.{event_name}.v{event_version}"
        in grammar
    )
    assert "producer=legacy_bridge" in grammar
    assert "aggregate_scope=non_aggregate" in grammar
    assert "not an aggregate identifier" in grammar.lower().replace("*", "")
    for subject in ACCEPTED_BRIDGE_OBSERVATION_SUBJECTS:
        assert subject in grammar
    assert "HUDHUD_LEGACY_BRIDGE" in grammar
    assert "MUST NOT" in grammar or "Do **not** introduce" in grammar


def test_accepted_bridge_observation_filters_match_non_aggregate_grammar() -> None:
    doc = load_topology_yaml("consumers.yaml")
    by_filter = {entry["filter_subject"]: entry for entry in doc["consumers"]}
    for subject, stream in ACCEPTED_BRIDGE_OBSERVATION_SUBJECTS.items():
        assert subject in by_filter, subject
        assert by_filter[subject]["stream"] == stream
        parts = subject.split(".")
        assert parts[0] == "hudhud"
        assert parts[2] == "legacy_bridge"
        assert parts[3] == "observation"
        assert parts[-1] == "v1"


def test_no_legacy_bridge_domain_stream() -> None:
    streams = load_topology_yaml("streams.yaml")
    names = {entry["name"] for entry in streams["streams"]}
    assert "HUDHUD_LEGACY_BRIDGE" not in names
    for entry in streams["streams"]:
        for subject in entry["subjects"]:
            assert not subject.startswith("hudhud.legacy_bridge")
    consumers = load_topology_yaml("consumers.yaml")
    for entry in consumers["consumers"]:
        assert not entry["filter_subject"].startswith("hudhud.legacy_bridge")
        assert entry["stream"] != "HUDHUD_LEGACY_BRIDGE"


def test_finance_stream_exists_wallet_is_projection_only() -> None:
    doc = load_topology_yaml("streams.yaml")
    finance = next(item for item in doc["streams"] if item["name"] == "HUDHUD_FINANCE")
    wallet = next(item for item in doc["streams"] if item["name"] == "HUDHUD_WALLET")
    assert "finance" in finance["subjects"][0]
    assert "projection" in wallet["description"].lower()
    assert "not financial authority" in wallet["description"].lower()


def test_no_delivery_to_wallet_authority_path() -> None:
    doc = load_topology_yaml("consumers.yaml")
    for entry in doc["consumers"]:
        assert entry["stream"] != "HUDHUD_WALLET"
        assert not entry["filter_subject"].startswith("hudhud.wallet.")


def test_runbook_states_not_ha_and_not_audit_store() -> None:
    runbook = (EVENTING_ROOT / "runbook.md").read_text(encoding="utf-8")
    assert "Not HA" in runbook or "not HA" in runbook
    assert "not legal" in runbook.lower() or "Not legal" in runbook
