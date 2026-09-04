"""Identity manifest static validation."""

from __future__ import annotations

import yaml

from .constants import (
    A1_SUBJECT,
    A2_SUBJECT,
    AUDIT_DURABLE,
    AUDIT_STREAM,
    IDENTITY_BOOTSTRAP,
    PICKUP_ACCEPTED_SUBJECT,
    PICKUP_JS_ACK,
    PICKUP_JS_FC,
    PICKUP_JS_INFO,
    PICKUP_JS_NEXT,
    PICKUP_STREAM,
    SHIPMENT_PICKUP_DURABLE,
    SHIPMENT_STREAM,
    TRACKING_DURABLE,
)
from .helpers import MANIFEST_FILE


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST_FILE.read_text(encoding="utf-8"))


def test_manifest_subjects_match_canonical_topology() -> None:
    manifest = _manifest()
    assert manifest["subjects"]["a1"] == A1_SUBJECT
    assert manifest["subjects"]["a2"] == A2_SUBJECT
    assert manifest["streams"]["shipment"] == SHIPMENT_STREAM
    assert manifest["streams"]["audit"] == AUDIT_STREAM
    assert manifest["consumers"]["tracking"] == TRACKING_DURABLE
    assert manifest["consumers"]["audit"] == AUDIT_DURABLE
    assert manifest["subjects"]["pickup_fact_accepted"] == PICKUP_ACCEPTED_SUBJECT
    assert manifest["streams"]["pickup"] == PICKUP_STREAM
    assert manifest["consumers"]["shipment_pickup_facts"] == SHIPMENT_PICKUP_DURABLE


def test_runtime_services_lack_topology_mutation_authority() -> None:
    manifest = _manifest()
    for identity in manifest["runtime_services_lack_topology_mutation"]:
        entry = manifest["identities"][identity]
        assert entry["topology_admin"] is False
        publish = entry.get("publish", [])
        assert not any(item.startswith("$JS.API.STREAM.") for item in publish)
        assert not any(item.startswith("$JS.API.CONSUMER.CREATE") for item in publish)


def test_bootstrap_may_not_publish_business_events() -> None:
    bootstrap = _manifest()["identities"][IDENTITY_BOOTSTRAP]
    assert "hudhud.>" in bootstrap["deny_publish"]
    assert bootstrap["topology_admin"] is True


def test_bridge_publish_allowlist_is_exact_a1_a2() -> None:
    bridge = _manifest()["identities"]["legacy-event-bridge"]
    assert set(bridge["publish"]) == {A1_SUBJECT, A2_SUBJECT}
    assert bridge["versions"] == ["v1", "v2"]


def test_audit_permissions_are_narrow_jetstream_api_paths() -> None:
    audit = _manifest()["identities"]["audit"]
    publish = audit["publish"]
    assert f"$JS.API.CONSUMER.INFO.{AUDIT_STREAM}.{AUDIT_DURABLE}" in publish
    assert f"$JS.API.CONSUMER.MSG.NEXT.{AUDIT_STREAM}.{AUDIT_DURABLE}" in publish
    assert f"$JS.ACK.{AUDIT_STREAM}.{AUDIT_DURABLE}.>" in publish
    assert "$JS.API.>" not in publish


def test_tracking_permissions_are_narrow_jetstream_api_paths() -> None:
    tracking = _manifest()["identities"]["tracking"]
    publish = tracking["publish"]
    assert f"$JS.API.CONSUMER.INFO.{SHIPMENT_STREAM}.{TRACKING_DURABLE}" in publish
    assert f"$JS.API.CONSUMER.MSG.NEXT.{SHIPMENT_STREAM}.{TRACKING_DURABLE}" in publish
    assert f"$JS.ACK.{SHIPMENT_STREAM}.{TRACKING_DURABLE}.>" in publish


def test_pickup_publish_allowlist_is_exact_accepted_subject() -> None:
    pickup = _manifest()["identities"]["pickup"]
    assert pickup["publish"] == [PICKUP_ACCEPTED_SUBJECT]
    assert pickup["subscribe"] == ["_INBOX.>"]
    assert pickup["versions"] == ["v1", "v2"]
    assert pickup["topology_admin"] is False
    assert "$JS.API.>" in pickup["deny_publish"]
    assert PICKUP_JS_NEXT in pickup["deny_publish"]
    assert "_INBOX.>" in pickup["subscribe"]


def test_shipment_permissions_are_narrow_jetstream_api_paths() -> None:
    shipment = _manifest()["identities"]["shipment"]
    publish = shipment["publish"]
    assert publish == [PICKUP_JS_INFO, PICKUP_JS_NEXT, PICKUP_JS_ACK]
    assert "_INBOX.>" in shipment["subscribe"]
    assert PICKUP_JS_FC in shipment["subscribe"]
    assert shipment["versions"] == ["v1", "v2"]
    assert shipment["topology_admin"] is False
    assert PICKUP_ACCEPTED_SUBJECT not in publish
    assert "$JS.API.>" not in publish
    assert "$JS.API.>" in shipment["deny_publish"]
    assert "$JS.API.CONSUMER.CREATE.>" in shipment["deny_publish"]


def test_revocation_manifest_documents_verified_propagation() -> None:
    revocation = _manifest()["revocation"]
    assert revocation["mechanism"].startswith("nsc delete user --revoke")
    assert "restart" in revocation["verified_propagation"]
    assert revocation["overlap_identities"]["pickup"] == "pickup-v1/v2"
    assert revocation["overlap_identities"]["shipment"] == "shipment-v1/v2"
    assert "one" in revocation["combined_revocation_restart"]
    manifest = _manifest()
    for identity, entry in manifest["identities"].items():
        publish = entry.get("publish", [])
        subscribe = entry.get("subscribe", [])
        assert "$JS.API.>" not in publish, identity
        assert "$JS.API.>" not in subscribe, identity
