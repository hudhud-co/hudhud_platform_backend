"""Merchant-stop reads: scan resolution, stop readiness, and acceptance status.

Everything in this module is read-only. That is the point of it: a driver standing at a
merchant's counter needs to know what a label *is* and whether their stop can close, and
neither question may create or change anything. An unregistered label must never be able
to become a shipment in the field, and a connection loss during acceptance must be
answerable without risking a second acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pickup.domain.entities import PickupTask
from pickup.domain.errors import (
    PickupBatchNotFound,
    PickupTaskNotFound,
    ScanIdentifierMissing,
)
from pickup.domain.value_objects import (
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ScanResolutionOutcome,
    StopOutcome,
)
from pickup.ports.authorization import PickupActor
from pickup.ports.repository import DriverWorkUnitOfWork


@dataclass(frozen=True, slots=True)
class ResolveScanCommand:
    assigned_batch_id: UUID
    scanned_identifier: str = ""
    #: The camera could not read the label. Reported by the device, not derivable here.
    unreadable: bool = False


@dataclass(frozen=True, slots=True)
class ScanResolution:
    outcome: ScanResolutionOutcome
    pickup_task_id: UUID | None = None
    shipment_id: UUID | None = None

    @property
    def can_proceed(self) -> bool:
        return self.outcome is ScanResolutionOutcome.VALID


@dataclass(frozen=True, slots=True)
class StopReadiness:
    """Counts of every expected parcel at one merchant stop."""

    assigned_batch_id: UUID
    expected: int
    accepted: int
    refused: int
    not_presented: int
    closed_by_recovery: int
    unresolved: int

    @property
    def can_complete(self) -> bool:
        """A stop closes only when every expected parcel has an outcome."""
        return self.expected > 0 and self.unresolved == 0

    @property
    def blocking_reason(self) -> str | None:
        if self.expected == 0:
            return "no parcels are expected at this stop"
        if self.unresolved:
            plural = "s" if self.unresolved > 1 else ""
            return (
                f"{self.unresolved} parcel{plural} still unresolved — "
                "every expected parcel needs an outcome"
            )
        return None

    @property
    def is_partial(self) -> bool:
        """A stop that closed with parcels that never entered custody."""
        return self.can_complete and (self.refused or self.not_presented) > 0


@dataclass(frozen=True, slots=True)
class AcceptanceStatus:
    """Answer to "was my acceptance recorded?" after a connection drop."""

    pickup_task_id: UUID
    recorded: bool
    acceptance_state: PickupTaskAcceptanceState | None
    accepted_at: object | None
    accepted_by_driver_user_id: str | None

    @property
    def safe_to_retry(self) -> bool:
        """True when the server has no record, so retrying creates no duplicate."""
        return not self.recorded


class PickupStopService:
    """Read-only merchant-stop queries for the assigned driver."""

    def __init__(self, unit_of_work: DriverWorkUnitOfWork) -> None:
        self._uow = unit_of_work

    def resolve_scan(
        self, command: ResolveScanCommand, *, actor: PickupActor
    ) -> ScanResolution:
        """Classify one scanned label against the driver's current stop.

        ``WRONG_MERCHANT`` is deliberately not produced here. Distinguishing "another
        merchant's label" from "this merchant's label at a different stop" needs merchant
        identity, which belongs to the ``merchant_store`` context and is not carried on a
        pickup task. Both collapse to ``NOT_IN_THIS_PICKUP``, which is the outcome that
        actually governs the driver's next move: do not accept it here.
        """
        if command.unreadable:
            # A label the camera cannot read is never resolved to a task: typing the code
            # or attaching a replacement label is not allowed.
            return ScanResolution(outcome=ScanResolutionOutcome.UNREADABLE)

        identifier = (command.scanned_identifier or "").strip()
        if not identifier:
            raise ScanIdentifierMissing()

        tasks = self._uow.pickup_tasks.list_tasks_for_driver(actor.actor_id)
        match = next((task for task in tasks if task.scanned_identifier == identifier), None)
        if match is None:
            match = next(
                (task for task in tasks if str(task.shipment_id) == identifier), None
            )
        if match is None:
            return ScanResolution(outcome=ScanResolutionOutcome.UNKNOWN_LABEL)

        found = ScanResolution(
            outcome=ScanResolutionOutcome.VALID,
            pickup_task_id=match.pickup_task_id,
            shipment_id=match.shipment_id,
        )
        if match.assigned_batch_id != command.assigned_batch_id:
            return _with(found, ScanResolutionOutcome.NOT_IN_THIS_PICKUP)
        if match.is_terminal:
            return _with(found, ScanResolutionOutcome.CANCELLED_SHIPMENT)
        if match.is_accepted:
            # Acceptance is recorded once; scanning it again changes nothing.
            return _with(found, ScanResolutionOutcome.ALREADY_ACCEPTED)
        if match.stop_outcome is not None:
            return _with(found, ScanResolutionOutcome.NOT_IN_THIS_PICKUP)
        if match.scanned_identifier == identifier and match.scanned_at is not None:
            return _with(found, ScanResolutionOutcome.DUPLICATE_SCAN)
        return found

    def stop_readiness(
        self, *, assigned_batch_id: UUID, actor: PickupActor
    ) -> StopReadiness:
        assigned = tuple(
            task
            for task in self._uow.pickup_tasks.list_tasks_for_driver(actor.actor_id)
            if task.assigned_batch_id == assigned_batch_id
        )
        if not assigned:
            raise PickupBatchNotFound(batch_id=str(assigned_batch_id))

        # A superseded attempt is not an expected parcel — its replacement is. Counting
        # both would show one physical parcel twice after a retry or reassignment.
        tasks = tuple(task for task in assigned if task.superseded_by_task_id is None)

        accepted = sum(1 for task in tasks if task.is_accepted)
        refused = sum(1 for task in tasks if task.stop_outcome is StopOutcome.REFUSED)
        not_presented = sum(
            1 for task in tasks if task.stop_outcome is StopOutcome.NOT_PRESENTED
        )
        closed_by_recovery = sum(
            1
            for task in tasks
            if task.status is PickupTaskStatus.CANCELLED and not task.is_accepted
        )
        unresolved = sum(1 for task in tasks if not task.is_stop_resolved)
        return StopReadiness(
            assigned_batch_id=assigned_batch_id,
            expected=len(tasks),
            accepted=accepted,
            refused=refused,
            not_presented=not_presented,
            closed_by_recovery=closed_by_recovery,
            unresolved=unresolved,
        )

    def acceptance_status(
        self, *, pickup_task_id: UUID, actor: PickupActor
    ) -> AcceptanceStatus:
        task = self._uow.pickup_tasks.get_pickup_task(pickup_task_id)
        if task is None or task.assigned_driver_user_id != actor.actor_id:
            # Same answer either way: a driver learns nothing about another driver's work.
            raise PickupTaskNotFound(str(pickup_task_id))
        return AcceptanceStatus(
            pickup_task_id=task.pickup_task_id,
            recorded=task.is_accepted,
            acceptance_state=task.acceptance_state,
            accepted_at=task.accepted_at,
            accepted_by_driver_user_id=task.accepted_by_driver_user_id,
        )


def _with(found: ScanResolution, outcome: ScanResolutionOutcome) -> ScanResolution:
    return ScanResolution(
        outcome=outcome,
        pickup_task_id=found.pickup_task_id,
        shipment_id=found.shipment_id,
    )


def unresolved_tasks(tasks: tuple[PickupTask, ...]) -> tuple[PickupTask, ...]:
    """Expected parcels that still need an outcome before the stop can close."""
    return tuple(task for task in tasks if not task.is_stop_resolved)
