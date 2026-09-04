"""Pickup acceptance + transactional outbox unit tests (W17-E)."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from pickup.application.acceptance_service import (
    AcceptPickupTaskCommand,
    PickupAcceptanceService,
)
from pickup.application.recovery_service import (
    PickupRecoveryService,
    RecoveryCommand,
    RegisterPickupTaskCommand,
)
from pickup.domain.errors import (
    AcceptanceOutcomeNotAllowed,
    ActingDriverMismatch,
    ConflictingIdempotencyKey,
    ExceptionEvidenceRequired,
    PickupConditionProofMissing,
    PickupTaskAlreadyAccepted,
    PickupTaskMissingAssignedBatch,
    PickupTaskMissingAssignedDriver,
    PickupTaskNotAcceptable,
    PickupTaskNotProofCaptured,
)
from pickup.domain.value_objects import (
    AcceptanceOutcome,
    EvidenceMediaRef,
    OutboxStatus,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ShipmentStatus,
)
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.memory import InMemoryPickupUnitOfWork, SimulatedCommitFailure
from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot


def _now() -> datetime:
    return datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _seed_ready_task(
    store: InMemoryPickupUnitOfWork,
    *,
    driver_id: str = "driver-42",
    has_proof: bool = True,
    status: PickupTaskStatus = PickupTaskStatus.PROOF_CAPTURED,
) -> UUID:
    recovery = PickupRecoveryService(store, InMemoryShipmentEligibilityAdapter())
    task_id = uuid4()
    shipment_id = uuid4()
    recovery.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=uuid4(),
            has_pickup_condition_proof=has_proof,
            status=status,
            created_at=_now(),
        )
    )
    return task_id


def _accept_command(
    task_id: UUID,
    *,
    outcome: AcceptanceOutcome = AcceptanceOutcome.ACCEPTED,
    driver_id: str = "driver-42",
    scanned: str = "WB-1001",
    key: str = "accept-1",
    media_refs: tuple[EvidenceMediaRef, ...] = (),
) -> AcceptPickupTaskCommand:
    return AcceptPickupTaskCommand(
        pickup_task_id=task_id,
        acting_driver_user_id=driver_id,
        scanned_identifier=scanned,
        outcome=outcome,
        idempotency_key=key,
        accepted_at=_now(),
        media_refs=media_refs,
        correlation_id=uuid4(),
    )


def test_accepted_commits_task_and_outbox_atomically() -> None:
    store = InMemoryPickupUnitOfWork()
    task_id = _seed_ready_task(store)
    service = PickupAcceptanceService(store)

    result = service.accept_pickup_task(_accept_command(task_id))

    task = store.pickup_tasks.get_pickup_task(task_id)
    assert task is not None
    assert task.acceptance_state is PickupTaskAcceptanceState.ACCEPTED
    assert task.accepted_at == _now()
    assert task.accepted_by_driver_user_id == "driver-42"
    assert task.version == 2
    assert result.event_id == result.outbox_record.event_id
    assert result.aggregate_version == 2
    pending = store.outbox.list_pending()
    assert len(pending) == 1
    assert pending[0].status is OutboxStatus.PENDING
    assert pending[0].aggregate_id == task_id
    assert pending[0].aggregate_version == 2
    assert pending[0].payload_json["event_type"] == "pickup.fact.accepted"
    assert pending[0].payload_json["aggregate_version"] == 2
    assert pending[0].subject == "hudhud.pickup.pickup.fact.accepted.v1"


def test_accepted_with_exception_requires_and_stores_media_refs() -> None:
    store = InMemoryPickupUnitOfWork()
    task_id = _seed_ready_task(store)
    service = PickupAcceptanceService(store)
    media = (
        EvidenceMediaRef(
            ref_type="s3",
            bucket="hudhud-evidence",
            key=f"pickup-evidence/{task_id}/exception-note.jpg",
            content_type="image/jpeg",
        ),
    )

    with pytest.raises(ExceptionEvidenceRequired):
        service.accept_pickup_task(
            _accept_command(
                task_id,
                outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
                key="ex-missing",
            )
        )

    result = service.accept_pickup_task(
        _accept_command(
            task_id,
            outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
            key="ex-ok",
            media_refs=media,
        )
    )
    assert result.pickup_task.acceptance_state is PickupTaskAcceptanceState.ACCEPTED_WITH_EXCEPTION
    refs = result.outbox_record.payload_json["media_refs"]
    assert len(refs) == 1
    assert refs[0]["key"].endswith("exception-note.jpg")
    assert "exception_evidence" not in result.outbox_record.payload_json["payload"]
    assert "inline_evidence" not in result.outbox_record.payload_json["payload"]


def test_prerequisites_proof_driver_batch_and_acting_driver() -> None:
    store = InMemoryPickupUnitOfWork()
    service = PickupAcceptanceService(store)

    no_proof = _seed_ready_task(store, has_proof=False)
    with pytest.raises(PickupConditionProofMissing):
        service.accept_pickup_task(_accept_command(no_proof, key="noproof"))

    pending = _seed_ready_task(store, status=PickupTaskStatus.PENDING)
    with pytest.raises(PickupTaskNotProofCaptured):
        service.accept_pickup_task(_accept_command(pending, key="pending"))

    mismatch = _seed_ready_task(store)
    with pytest.raises(ActingDriverMismatch):
        service.accept_pickup_task(
            _accept_command(mismatch, driver_id="other-driver", key="mismatch")
        )

    empty_driver = _seed_ready_task(store, driver_id="driver-ok")
    task = store.pickup_tasks.get_pickup_task(empty_driver)
    assert task is not None
    task.assigned_driver_user_id = "   "
    store.pickup_tasks.save_pickup_task(task)
    with pytest.raises(PickupTaskMissingAssignedDriver):
        service.accept_pickup_task(
            _accept_command(empty_driver, driver_id="   ", key="empty-driver")
        )

    no_batch = _seed_ready_task(store)
    task = store.pickup_tasks.get_pickup_task(no_batch)
    assert task is not None
    task.assigned_batch_id = None
    store.pickup_tasks.save_pickup_task(task)
    with pytest.raises(PickupTaskMissingAssignedBatch):
        service.accept_pickup_task(_accept_command(no_batch, key="nobatch"))


def test_rejected_cancelled_superseded_and_already_accepted_paths() -> None:
    store = InMemoryPickupUnitOfWork()
    service = PickupAcceptanceService(store)

    with pytest.raises(AcceptanceOutcomeNotAllowed):
        service.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=uuid4(),
                acting_driver_user_id="driver-42",
                scanned_identifier="WB-1",
                outcome="REJECTED",
                idempotency_key="rej",
                accepted_at=_now(),
            )
        )

    cancelled = _seed_ready_task(store)
    task = store.pickup_tasks.get_pickup_task(cancelled)
    assert task is not None
    task.status = PickupTaskStatus.CANCELLED
    store.pickup_tasks.save_pickup_task(task)
    with pytest.raises(PickupTaskNotAcceptable):
        service.accept_pickup_task(_accept_command(cancelled, key="cancel"))

    superseded = _seed_ready_task(store)
    task = store.pickup_tasks.get_pickup_task(superseded)
    assert task is not None
    task.status = PickupTaskStatus.SUPERSEDED
    store.pickup_tasks.save_pickup_task(task)
    with pytest.raises(PickupTaskNotAcceptable):
        service.accept_pickup_task(_accept_command(superseded, key="super"))

    accepted = _seed_ready_task(store)
    service.accept_pickup_task(_accept_command(accepted, key="first"))
    with pytest.raises(PickupTaskAlreadyAccepted):
        service.accept_pickup_task(_accept_command(accepted, key="second"))

    rejected_local = _seed_ready_task(store)
    task = store.pickup_tasks.get_pickup_task(rejected_local)
    assert task is not None
    task.acceptance_state = PickupTaskAcceptanceState.REJECTED
    store.pickup_tasks.save_pickup_task(task)
    with pytest.raises(PickupTaskAlreadyAccepted):
        service.accept_pickup_task(_accept_command(rejected_local, key="after-reject"))
    assert store.outbox.list_for_aggregate(rejected_local) == ()


def test_exact_contract_envelope_and_aggregate_version_ownership() -> None:
    store = InMemoryPickupUnitOfWork()
    task_id = _seed_ready_task(store)
    service = PickupAcceptanceService(store)
    result = service.accept_pickup_task(_accept_command(task_id))
    envelope = result.outbox_record.payload_json

    assert envelope["producer"] == "pickup"
    assert envelope["event_type"] == "pickup.fact.accepted"
    assert envelope["event_version"] == 1
    assert envelope["message_kind"] == "integration"
    assert envelope["aggregate_type"] == "pickup_task"
    assert envelope["aggregate_id"] == str(task_id)
    assert envelope["aggregate_version"] == result.aggregate_version
    assert envelope["aggregate_version"] == result.pickup_task.version
    assert "shipment_aggregate_version" not in envelope["payload"]
    assert set(envelope["payload"]) == {
        "pickup_task_id",
        "shipment_id",
        "outcome",
        "accepted_at",
        "assigned_driver_user_id",
        "acting_driver_user_id",
        "scanned_identifier",
    }


def test_stable_event_id_on_replay_and_conflicting_key() -> None:
    store = InMemoryPickupUnitOfWork()
    task_id = _seed_ready_task(store)
    service = PickupAcceptanceService(store)
    command = _accept_command(task_id, key="idem-1")

    first = service.accept_pickup_task(command)
    second = service.accept_pickup_task(command)
    assert second.idempotent_replay is True
    assert second.event_id == first.event_id
    assert len(store.outbox.list_for_aggregate(task_id)) == 1

    conflicting = AcceptPickupTaskCommand(
        pickup_task_id=task_id,
        acting_driver_user_id="driver-42",
        scanned_identifier="WB-OTHER",
        outcome=AcceptanceOutcome.ACCEPTED,
        idempotency_key="idem-1",
        accepted_at=_now(),
    )
    with pytest.raises(ConflictingIdempotencyKey):
        service.accept_pickup_task(conflicting)


def test_rollback_leaves_task_and_outbox_unchanged() -> None:
    store = InMemoryPickupUnitOfWork()
    task_id = _seed_ready_task(store)
    service = PickupAcceptanceService(store)
    store.fail_on_commit = True

    with pytest.raises(SimulatedCommitFailure):
        service.accept_pickup_task(_accept_command(task_id, key="rollback"))

    task = store.pickup_tasks.get_pickup_task(task_id)
    assert task is not None
    assert task.acceptance_state is None
    assert task.version == 1
    assert store.outbox.list_pending() == ()
    assert store.acceptance_idempotency.get_record("rollback") is None


def test_concurrent_acceptance_produces_at_most_one_outbox() -> None:
    store = InMemoryPickupUnitOfWork()
    task_id = _seed_ready_task(store)
    barrier = Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def _worker(key: str) -> None:
        service = PickupAcceptanceService(store)
        barrier.wait()
        try:
            results.append(service.accept_pickup_task(_accept_command(task_id, key=key)))
        except BaseException as exc:  # noqa: BLE001 — collect for assertion
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_worker, "c1"), pool.submit(_worker, "c2")]
        for future in futures:
            future.result()

    successes = [item for item in results if item is not None]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], PickupTaskAlreadyAccepted)
    assert len(store.outbox.list_for_aggregate(task_id)) == 1
    task = store.pickup_tasks.get_pickup_task(task_id)
    assert task is not None
    assert task.is_accepted
    assert task.version == 2


def test_recovery_still_rejects_accepted_tasks() -> None:
    store = InMemoryPickupUnitOfWork()
    eligibility = InMemoryShipmentEligibilityAdapter()
    task_id = _seed_ready_task(store)
    task = store.pickup_tasks.get_pickup_task(task_id)
    assert task is not None
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=task.shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )
    acceptance = PickupAcceptanceService(store)
    acceptance.accept_pickup_task(_accept_command(task_id, key="acc"))
    recovery = PickupRecoveryService(store, eligibility)
    with pytest.raises(PickupTaskAlreadyAccepted):
        recovery.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task_id,
                idempotency_key="retry-after-accept",
                reason="should fail",
                occurred_at=_now(),
            )
        )


def test_no_shipment_import_in_acceptance_modules() -> None:
    roots = [
        Path(__file__).resolve().parents[1] / "src" / "pickup" / "application",
        Path(__file__).resolve().parents[1] / "src" / "pickup" / "infrastructure" / "contracts",
    ]
    for root in roots:
        for py_file in root.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] != "shipment"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] != "shipment"
