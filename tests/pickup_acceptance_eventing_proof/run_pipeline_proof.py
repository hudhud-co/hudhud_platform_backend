"""Single 120s local disposable runtime proof for Pickup→Shipment acceptance."""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from pickup_acceptance_eventing_proof.constants import (  # noqa: E402
    ACK_POLICY,
    HANDLER_CONCURRENCY,
    PICKUP_DATABASE,
    PICKUP_DURABLE,
    PICKUP_EXPECTED_HEAD,
    PICKUP_STREAM,
    PICKUP_SUBJECT,
    SHIPMENT_DATABASE,
    SHIPMENT_EXPECTED_HEAD,
)
from pickup_acceptance_eventing_proof.helpers import (  # noqa: E402
    LAB_ROOT,
    alembic_current_revision,
    build_nats_url,
    compose_down,
    compose_up,
    dedicated_resources_absent,
    leftover_pytest_processes,
    leftover_worker_processes,
    outbox_status_for_event,
    pickup_service_url,
    prepare_service_databases,
    run_jetstream_publish_raw,
    run_outbox_republish,
    run_pickup_accept,
    run_pickup_relay_once,
    run_shipment_inspect,
    run_shipment_poll_once,
    run_shipment_seed,
    shipment_service_url,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _poll_until_processed(
    *,
    shipment_url: str,
    nats_url: str,
    attempts: int = 5,
    fail_first_ack: bool = False,
    fail_quarantine_persist: bool = False,
) -> dict[str, object]:
    last: dict[str, object] | None = None
    for _ in range(attempts):
        last = run_shipment_poll_once(
            database_url=shipment_url,
            nats_url=nats_url,
            fail_first_ack=fail_first_ack,
            fail_quarantine_persist=fail_quarantine_persist,
        )
        if int(last["processed"]) >= 1:
            return last
    assert last is not None
    return last


def _assert_binding(poll: dict[str, object]) -> None:
    _assert(poll["binding_stream"] == PICKUP_STREAM, "durable stream mismatch")
    _assert(poll["binding_durable"] == PICKUP_DURABLE, "durable name mismatch")
    _assert(poll["binding_filter"] == PICKUP_SUBJECT, "durable filter mismatch")
    _assert(poll["binding_ack_policy"] == ACK_POLICY, "AckPolicy mismatch")
    _assert(poll["handler_concurrency"] == HANDLER_CONCURRENCY, "handler concurrency")
    _assert(poll["active_batch"] == 0, "worker task collection must drain")


def _scenario_success_and_lost_ack(
    pickup_url: str, shipment_url: str, nats_url: str
) -> dict[str, object]:
    shipment_id = str(uuid4())
    driver_id = "drv-success"
    waybill = "WB-SUCCESS-001"
    run_shipment_seed(database_url=shipment_url, shipment_id=shipment_id, waybill=waybill)
    accepted = run_pickup_accept(
        database_url=pickup_url,
        shipment_id=shipment_id,
        driver_id=driver_id,
        waybill=waybill,
        idempotency_key="accept-success-001",
    )
    event_id = str(accepted["event_id"])
    _assert(accepted["acceptance_state"] == "ACCEPTED", "pickup task not accepted")
    _assert(accepted["outbox_status"] == "pending", "outbox not committed pending")
    _assert(outbox_status_for_event(event_id) == "pending", "outbox row missing")

    relay = run_pickup_relay_once(database_url=pickup_url, nats_url=nats_url)
    _assert(relay["puback_received"] is True, "PubAck missing")
    _assert(relay["puback_stream"] == PICKUP_STREAM, "PubAck stream mismatch")
    _assert(relay["published_count"] == 1, "expected one published outbox row")
    _assert(outbox_status_for_event(event_id) == "published", "outbox not published")

    first = run_shipment_poll_once(
        database_url=shipment_url,
        nats_url=nats_url,
        fail_first_ack=True,
    )
    _assert_binding(first)
    _assert(int(first["processed"]) == 1, "first poll did not process")
    _assert("ack_failed" in first["broker_actions"], "ACK was not withheld until after commit")
    after_commit = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=shipment_id,
        event_id=event_id,
        expected_custody_id=driver_id,
    )
    _assert(after_commit["is_in_custody"] is True, "shipment not IN_CUSTODY after commit")
    _assert(after_commit["custody_type_pickup_driver"] is True, "custody type")
    _assert(after_commit["custody_id_matches"] is True, "custody id")
    _assert(after_commit["has_accepted_at"] is True, "accepted_at")
    _assert(after_commit["has_sla_started_at"] is True, "sla_started_at")
    _assert(after_commit["has_decision"] is True, "acceptance decision")
    _assert(after_commit["event_count"] == 1, "shipment event")
    _assert(after_commit["audit_count"] == 1, "audit row")
    _assert(after_commit["inbox_status"] == "processed", "inbox not processed before ACK")

    second = _poll_until_processed(shipment_url=shipment_url, nats_url=nats_url)
    _assert_binding(second)
    _assert(int(second["processed"]) == 1, "redelivery not processed")
    _assert("ack" in second["broker_actions"], "duplicate delivery not ACKed")
    after_ack = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=shipment_id,
        event_id=event_id,
        expected_custody_id=driver_id,
    )
    _assert(after_ack["event_count"] == 1, "second transition on lost ACK")
    _assert(after_ack["audit_count"] == 1, "second audit on lost ACK")
    _assert(after_ack["inbox_status"] == "processed", "inbox after redelivery")

    replay = run_outbox_republish(
        database_url=pickup_url,
        nats_url=nats_url,
        event_id=event_id,
        msg_id=f"replay-{uuid4()}",
    )
    _assert(replay["puback_received"] is True, "replay PubAck missing")
    third = _poll_until_processed(shipment_url=shipment_url, nats_url=nats_url)
    _assert(int(third["processed"]) == 1, "replay not consumed")
    _assert("ack" in third["broker_actions"], "replay not ACKed")
    after_replay = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=shipment_id,
        event_id=event_id,
        expected_custody_id=driver_id,
    )
    _assert(after_replay["event_count"] == 1, "second transition on replay")
    _assert(after_replay["audit_count"] == 1, "second audit on replay")
    _assert(after_replay["inbox_status"] == "processed", "replay inbox")
    return {"event_id": event_id, "ok": True}


def _scenario_exception(pickup_url: str, shipment_url: str, nats_url: str) -> None:
    shipment_id = str(uuid4())
    driver_id = "drv-exception"
    waybill = "WB-EXC-001"
    run_shipment_seed(database_url=shipment_url, shipment_id=shipment_id, waybill=waybill)
    accepted = run_pickup_accept(
        database_url=pickup_url,
        shipment_id=shipment_id,
        driver_id=driver_id,
        waybill=waybill,
        idempotency_key="accept-exception-001",
        outcome="ACCEPTED_WITH_EXCEPTION",
        media_key=f"pickup-evidence/{shipment_id}/exception-note.jpg",
    )
    _assert(accepted["acceptance_state"] == "ACCEPTED_WITH_EXCEPTION", "exception outcome")
    _assert(accepted["media_ref_count"] == 1, "media_refs missing after accept")
    _assert(accepted["has_inline_media"] is False, "inline media present")
    relay = run_pickup_relay_once(database_url=pickup_url, nats_url=nats_url)
    _assert(relay["puback_received"] is True, "exception PubAck")
    poll = run_shipment_poll_once(database_url=shipment_url, nats_url=nats_url)
    _assert(int(poll["processed"]) == 1, "exception fact not processed")
    _assert("ack" in poll["broker_actions"], "exception not ACKed")
    inspected = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=shipment_id,
        event_id=str(accepted["event_id"]),
        expected_custody_id=driver_id,
    )
    _assert(inspected["is_in_custody"] is True, "exception shipment not in custody")
    _assert(inspected["exception_evidence_count"] == 1, "exception evidence not persisted")
    _assert(
        inspected["decision_outcome"] == "accepted_with_exception",
        "decision outcome",
    )
    _assert(accepted["has_inline_media"] is False, "inline media after transport")


def _scenario_invalid(pickup_url: str, shipment_url: str, nats_url: str) -> None:
    poison_id = str(uuid4())
    published = run_jetstream_publish_raw(
        nats_url=nats_url,
        subject=PICKUP_SUBJECT,
        payload=b"{not-json",
        msg_id=poison_id,
    )
    _assert(published["stream"] == PICKUP_STREAM, "poison stream")
    poll = run_shipment_poll_once(database_url=shipment_url, nats_url=nats_url)
    _assert(int(poll["processed"]) == 1, "poison not handled")
    _assert("ack" in poll["broker_actions"], "poison ACK after quarantine")
    inspected = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=str(uuid4()),
        event_id=poison_id,
        expected_custody_id="unused",
    )
    _assert(inspected["inbox_status"] == "quarantined", "poison not quarantined")
    _assert(inspected["inbox_error_code"] == "DESERIALIZE_FAILURE", "poison error code")

    shipment_id = str(uuid4())
    driver_id = "drv-mismatch"
    run_shipment_seed(
        database_url=shipment_url,
        shipment_id=shipment_id,
        waybill="WB-EXPECTED-001",
    )
    accepted = run_pickup_accept(
        database_url=pickup_url,
        shipment_id=shipment_id,
        driver_id=driver_id,
        waybill="WB-OTHER-001",
        idempotency_key="accept-mismatch-001",
    )
    relay = run_pickup_relay_once(database_url=pickup_url, nats_url=nats_url)
    _assert(relay["puback_received"] is True, "mismatch PubAck")
    mismatch_poll = run_shipment_poll_once(database_url=shipment_url, nats_url=nats_url)
    _assert("ack" in mismatch_poll["broker_actions"], "mismatch ACK after quarantine")
    mismatch = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=shipment_id,
        event_id=str(accepted["event_id"]),
        expected_custody_id=driver_id,
    )
    _assert(mismatch["is_in_custody"] is False, "mismatch mutated shipment")
    _assert(mismatch["status"] == "CREATED", "mismatch status")
    _assert(mismatch["event_count"] == 0, "mismatch wrote event")
    _assert(mismatch["inbox_status"] == "quarantined", "mismatch not quarantined")
    _assert(mismatch["inbox_error_code"] == "SCANNED_IDENTIFIER_MISMATCH", "mismatch code")


def _scenario_quarantine_nak(shipment_url: str, nats_url: str) -> None:
    poison_id = str(uuid4())
    run_jetstream_publish_raw(
        nats_url=nats_url,
        subject=PICKUP_SUBJECT,
        payload=b"{not-json-again",
        msg_id=poison_id,
    )
    first = run_shipment_poll_once(
        database_url=shipment_url,
        nats_url=nats_url,
        fail_quarantine_persist=True,
    )
    _assert("nak" in first["broker_actions"], "quarantine persist failure did not NAK")
    before = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=str(uuid4()),
        event_id=poison_id,
        expected_custody_id="unused",
    )
    _assert(before["inbox_status"] != "quarantined", "quarantine persisted despite failure")
    second = _poll_until_processed(shipment_url=shipment_url, nats_url=nats_url)
    _assert("ack" in second["broker_actions"], "retry quarantine not ACKed")
    after = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=str(uuid4()),
        event_id=poison_id,
        expected_custody_id="unused",
    )
    _assert(after["inbox_status"] == "quarantined", "retry did not quarantine")


def _scenario_restart(pickup_url: str, shipment_url: str, nats_url: str) -> None:
    shipment_id = str(uuid4())
    driver_id = "drv-restart"
    waybill = "WB-RESTART-001"
    run_shipment_seed(database_url=shipment_url, shipment_id=shipment_id, waybill=waybill)
    accepted = run_pickup_accept(
        database_url=pickup_url,
        shipment_id=shipment_id,
        driver_id=driver_id,
        waybill=waybill,
        idempotency_key="accept-restart-001",
    )
    event_id = str(accepted["event_id"])
    relay = run_pickup_relay_once(database_url=pickup_url, nats_url=nats_url)
    _assert(relay["puback_received"] is True, "restart PubAck")
    first = run_shipment_poll_once(database_url=shipment_url, nats_url=nats_url)
    _assert(int(first["processed"]) == 1, "restart first poll")
    _assert(int(first["active_batch"]) == 0, "growing task collection")
    idle = run_shipment_poll_once(database_url=shipment_url, nats_url=nats_url)
    _assert(int(idle["processed"]) == 0, "busy loop after drain")
    _assert(int(idle["active_batch"]) == 0, "active batch after reconnect")
    inspected = run_shipment_inspect(
        database_url=shipment_url,
        shipment_id=shipment_id,
        event_id=event_id,
        expected_custody_id=driver_id,
    )
    _assert(inspected["inbox_status"] == "processed", "processed evidence lost on reconnect")
    _assert(inspected["is_in_custody"] is True, "canonical state lost on reconnect")


def main() -> int:
    results: dict[str, object] = {"ok": False, "scenarios": {}}
    started = False
    try:
        postgres_port, nats_port = compose_up()
        started = True
        prepare_service_databases(postgres_port)
        _assert(
            alembic_current_revision(PICKUP_DATABASE) == PICKUP_EXPECTED_HEAD,
            "pickup migration head",
        )
        _assert(
            alembic_current_revision(SHIPMENT_DATABASE) == SHIPMENT_EXPECTED_HEAD,
            "shipment migration head",
        )
        pickup_url = pickup_service_url(postgres_port)
        shipment_url = shipment_service_url(postgres_port)
        nats_url = build_nats_url(port=nats_port)

        _scenario_success_and_lost_ack(pickup_url, shipment_url, nats_url)
        results["scenarios"]["success_replay_lost_ack"] = True
        _scenario_exception(pickup_url, shipment_url, nats_url)
        results["scenarios"]["accepted_with_exception"] = True
        _scenario_invalid(pickup_url, shipment_url, nats_url)
        results["scenarios"]["invalid_conflicting"] = True
        _scenario_quarantine_nak(shipment_url, nats_url)
        results["scenarios"]["quarantine_persist_nak"] = True
        _scenario_restart(pickup_url, shipment_url, nats_url)
        results["scenarios"]["restart"] = True
        results["ok"] = True
        results["max_containers"] = 2
        results["handler_concurrency"] = HANDLER_CONCURRENCY
        results["production_ready"] = False
        print(json.dumps(results))
        print("HUDHUD_PICKUP_ACCEPTANCE_EVENTING_PROOF_COMPLETE")
        return 0
    except Exception as exc:
        results["ok"] = False
        results["error_code"] = type(exc).__name__
        print(json.dumps(results))
        traceback.print_exc()
        return 1
    finally:
        if started or not dedicated_resources_absent():
            cleanup = LAB_ROOT / "scripts" / "cleanup.sh"
            compose_down()
            if cleanup.is_file():
                subprocess.run(["sh", str(cleanup)], capture_output=True, text=True, check=False)
        workers = leftover_worker_processes()
        pytest_left = leftover_pytest_processes()
        if not dedicated_resources_absent() or workers or pytest_left:
            print("cleanup incomplete", file=sys.stderr)
            if results.get("ok") is True:
                raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
