"""Architecture fitness tests for HUDHUD platform monorepo."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "quality" / "verify_boundaries.py"
BOUNDARIES_FILE = REPO_ROOT / "architecture" / "service-boundaries.yaml"
INVARIANTS_FILE = REPO_ROOT / "architecture" / "invariants.md"
OWNERSHIP_FILE = REPO_ROOT / "architecture" / "ownership-matrix.yaml"


@pytest.fixture(scope="module")
def boundaries() -> dict:
    with BOUNDARIES_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def ownership_matrix() -> dict:
    with OWNERSHIP_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_verify_boundaries_script_passes() -> None:
    """End-to-end architecture verifier must pass."""
    result = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_service_boundaries_manifest_exists(boundaries: dict) -> None:
    assert boundaries.get("version") == 1
    contexts = boundaries.get("bounded_contexts", {})
    assert len(contexts) >= 20
    assert "shipment" in contexts
    assert "gateway" in contexts


def test_shipment_sole_lifecycle_writer_invariant(boundaries: dict) -> None:
    shipment = boundaries["bounded_contexts"]["shipment"]
    prerequisites = shipment.get("policy_prerequisites", [])
    assert "shipment_sole_lifecycle_writer" in prerequisites
    assert shipment["data_ownership"]["strategy"] == "dedicated_database"


def test_gateway_does_not_own_domain_tables(boundaries: dict) -> None:
    gateway = boundaries["bounded_contexts"]["gateway"]
    assert gateway["data_ownership"]["strategy"] == "none"
    assert gateway["data_ownership"]["legacy_tables"] == []


def test_hub_and_linehaul_remain_separate(boundaries: dict) -> None:
    contexts = boundaries["bounded_contexts"]
    assert "hub" in contexts
    assert "linehaul" in contexts
    assert contexts["hub"]["proposed_platform_owner"] == "hub"
    assert contexts["linehaul"]["proposed_platform_owner"] == "linehaul"


def test_finance_is_policy_blocked(boundaries: dict) -> None:
    finance = boundaries["bounded_contexts"]["finance_settlement"]
    assert finance["transitional_deployable_candidate"] == "policy_blocked"
    assert finance["extraction_status"] == "not_started"


def test_legacy_reference_is_read_only(boundaries: dict) -> None:
    legacy = boundaries.get("legacy_reference", {})
    assert legacy.get("runtime_dependency") == "forbidden"
    assert legacy.get("role") == "read_only_reference"


def test_ownership_matrix_shipment_invariant(ownership_matrix: dict) -> None:
    shipment = ownership_matrix["ownership"]["shipment"]
    assert shipment["canonical_writer"] == "shipment"
    assert "sole_lifecycle_state_writer" in shipment["invariants"]


def test_invariants_document_exists() -> None:
    content = INVARIANTS_FILE.read_text(encoding="utf-8")
    assert "Shipment" in content
    assert "NATS JetStream" in content
    assert "One-writer cutover" in content


def test_no_root_alembic_migrations() -> None:
    root_alembic = REPO_ROOT / "alembic" / "versions"
    if root_alembic.is_dir():
        migrations = list(root_alembic.glob("*.py"))
        assert migrations == [], "Root-level migrations forbidden; use service-owned alembic"


def test_services_directory_has_readme() -> None:
    readme = REPO_ROOT / "services" / "README.md"
    assert readme.exists()


def test_packages_directory_has_readme() -> None:
    readme = REPO_ROOT / "packages" / "README.md"
    assert readme.exists()


def test_legacy_event_bridge_is_not_a_bounded_context(boundaries: dict) -> None:
    contexts = boundaries.get("bounded_contexts", {})
    assert "legacy_event_bridge" not in contexts
    assert "legacy_bridge" not in contexts
    bridge = boundaries["legacy_event_bridge"]
    assert bridge["classification"] == "transitional_technical_deployable"
    assert bridge["bounded_context"] is False
    assert bridge["canonical_aggregate_owner"] is False
    assert bridge["role"] == "cdc_adapter_publisher"
    assert bridge["authority"] == "non_authoritative"
    assert bridge["proposed_platform_owner"] == "none"
    notes = bridge["data_ownership"]["notes"].lower()
    assert "not a bounded context" in notes
    assert "outbox" in notes
    assert bridge["data_ownership"]["domain_lifecycle"] == "none"
    forbidden = set(bridge.get("forbidden", []))
    assert "bounded_context_status" in forbidden
    assert "canonical_aggregate_ownership" in forbidden
    assert "domain_lifecycle_ownership" in forbidden


def test_legacy_event_bridge_is_not_an_ownership_bounded_context(
    ownership_matrix: dict,
) -> None:
    assert "legacy_event_bridge" not in ownership_matrix["ownership"]
    assert "legacy_bridge" not in ownership_matrix["ownership"]
    bridge = ownership_matrix["legacy_event_bridge"]
    assert bridge["classification"] == "transitional_technical_deployable"
    assert bridge["bounded_context"] is False
    assert bridge["canonical_aggregate_owner"] is False
    assert bridge["canonical_writer"] == "none"
    assert bridge["domain_database_owner"] == "none"
    assert bridge["technical_state_scope"] == "landing_checkpoint_outbox"
    assert bridge["retires_per_context"] is True


def test_tracking_bootstrap_registered(boundaries: dict, ownership_matrix: dict) -> None:
    tracking = boundaries["bounded_contexts"]["tracking"]
    assert tracking["proposed_platform_owner"] == "tracking"
    assert tracking["extraction_status"] == "bootstrap_observation_projection"
    assert tracking["runtime_evidence"]["production_ready"] is False
    assert "legacy_bridge.observation.shipment_timeline_entry" in tracking["consumed_events"]
    ownership = ownership_matrix["ownership"]["tracking"]
    assert ownership["database_owner"] == "tracking"
    assert ownership["canonical_writer"] == "none"


def test_shipment_domain_foundation_bootstrap_registered(
    boundaries: dict,
    ownership_matrix: dict,
) -> None:
    shipment = boundaries["bounded_contexts"]["shipment"]
    assert shipment["proposed_platform_owner"] == "shipment"
    assert shipment["extraction_status"] == "bootstrap_domain_foundation"
    evidence = shipment["runtime_evidence"]
    assert evidence["implementation"] == "bootstrap_acceptance_lifecycle_foundation"
    assert evidence["scope"] == "order_intent_through_acceptance_scan"
    assert evidence["production_ready"] is False
    ownership = ownership_matrix["ownership"]["shipment"]
    assert ownership["canonical_writer"] == "shipment"
    assert (
        ownership["runtime_evidence"]["implementation"]
        == "bootstrap_acceptance_lifecycle_foundation"
    )


def test_pickup_recovery_foundation_bootstrap_registered(
    boundaries: dict,
    ownership_matrix: dict,
) -> None:
    pickup = boundaries["bounded_contexts"]["pickup"]
    assert pickup["proposed_platform_owner"] == "pickup"
    assert pickup["extraction_status"] == "bootstrap_domain_foundation"
    evidence = pickup["runtime_evidence"]
    assert evidence["implementation"] == "bootstrap_recovery_lifecycle_foundation"
    assert evidence["scope"] == "pickup_task_recovery_and_attempt_history"
    assert evidence["production_ready"] is False
    assert pickup["allowed_dependencies"] == []
    ownership = ownership_matrix["ownership"]["pickup"]
    assert ownership["canonical_writer"] == "pickup"
    assert (
        ownership["runtime_evidence"]["implementation"]
        == "bootstrap_recovery_lifecycle_foundation"
    )


def test_wave17_acceptance_boundary_foundation_registered(
    boundaries: dict,
    ownership_matrix: dict,
) -> None:
    """W17 shared metadata: contract registered; adapters deferred; not production."""
    wave17 = boundaries["wave_17_runtime_evidence"]
    assert wave17["production_ready"] is False
    assert wave17["classifications"]["contract_registered"] == "pickup.fact.accepted.v1"
    assert (
        wave17["classifications"]["implementation_status"]
        == "implementation_authorized_not_production_enabled"
    )
    assert wave17["classifications"]["evidence_class"] == "local_unit_static_only"
    assert (
        wave17["classifications"]["http_acceptance_path"]
        == "compatibility_internal_not_second_production_writer"
    )
    limitations = set(wave17["limitations"])
    assert "pickup_outbox_producer_adapter_deferred" in limitations
    assert "shipment_inbox_consumer_adapter_deferred" in limitations
    assert "shipment_custody_migration_disposable_postgres_proof_pending" in limitations
    assert "no_nats_topology_changes_this_round" in limitations

    shipment_w17 = wave17["services"]["shipment"]
    assert shipment_w17["pickup_fact_accepted_consumer"] == "deferred"
    assert shipment_w17["custody_type_canonical"] == "PICKUP_DRIVER"
    assert shipment_w17["custody_data_migration"] == "implemented_alembic_single_head"
    assert shipment_w17["custody_migration_disposable_postgres_proof"] == "pending"
    assert shipment_w17["http_acceptance_api"] == "compatibility_internal"
    assert shipment_w17["production_ready"] is False

    pickup_w17 = wave17["services"]["pickup"]
    assert pickup_w17["pickup_fact_accepted_producer"] == "deferred"
    assert pickup_w17["recovery_eligibility_blocks_on"] == "PICKUP_DRIVER"
    assert pickup_w17["contract_aggregate_authority"] == "pickup_task"
    assert pickup_w17["production_ready"] is False

    shipment = boundaries["bounded_contexts"]["shipment"]
    shipment_evidence = shipment["runtime_evidence"]
    assert shipment_evidence["pickup_fact_accepted_consumer"] == "deferred"
    assert shipment_evidence["custody_type_canonical"] == "PICKUP_DRIVER"
    assert shipment_evidence["http_acceptance_api"] == "compatibility_internal"
    assert shipment_evidence["production_ready"] is False
    assert "pickup.fact.accepted" in shipment["consumed_events"]
    assert shipment["data_ownership"]["strategy"] == "dedicated_database"

    pickup = boundaries["bounded_contexts"]["pickup"]
    pickup_evidence = pickup["runtime_evidence"]
    assert pickup_evidence["pickup_fact_accepted_producer"] == "deferred"
    assert (
        pickup_evidence["pickup_fact_accepted_contract"]
        == "registered_not_production_enabled"
    )
    assert pickup_evidence["recovery_eligibility_blocks_on"] == "PICKUP_DRIVER"
    assert pickup_evidence["production_ready"] is False
    assert "pickup.fact.accepted" in pickup["published_events"]
    assert pickup["allowed_dependencies"] == []

    ownership_shipment = ownership_matrix["ownership"]["shipment"]
    assert ownership_shipment["canonical_writer"] == "shipment"
    assert "sole_lifecycle_state_writer" in ownership_shipment["invariants"]
    assert ownership_shipment["runtime_evidence"]["pickup_fact_accepted_consumer"] == "deferred"
    assert ownership_shipment["runtime_evidence"]["production_ready"] is False

    ownership_pickup = ownership_matrix["ownership"]["pickup"]
    assert ownership_pickup["canonical_writer"] == "pickup"
    assert ownership_pickup["runtime_evidence"]["pickup_fact_accepted_producer"] == "deferred"
    assert (
        ownership_pickup["runtime_evidence"]["pickup_fact_accepted_contract"]
        == "registered_not_production_enabled"
    )
    assert ownership_pickup["runtime_evidence"]["recovery_eligibility_blocks_on"] == (
        "PICKUP_DRIVER"
    )
    assert ownership_pickup["runtime_evidence"]["production_ready"] is False


def test_messaging_conformance_is_allowlisted_and_technical(boundaries: dict) -> None:
    allowed = boundaries["shared_packages"]["allowed_categories"]
    assert "messaging_conformance" in allowed
    package_dir = REPO_ROOT / "packages" / "messaging_conformance"
    assert package_dir.is_dir()
    pyproject = package_dir / "pyproject.toml"
    assert pyproject.exists()
    content = pyproject.read_text(encoding="utf-8")
    assert "sqlalchemy" not in content.lower()
    assert "alembic" not in content.lower()
