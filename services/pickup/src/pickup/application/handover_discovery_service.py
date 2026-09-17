"""Sender-facing discovery for the sender→courier handover ceremony.

The sender holds a shipment, not a pickup task. Without this read it has no way to
learn that a ceremony is open, which half is outstanding, or whether it may act —
leaving the app to enumerate pickup tasks or hard-code identifiers. The projection
answers that from current server state and carries no challenge payload, no courier
identity and no driver detail: it is a readiness signal, never evidence.

The flags here are **informational only**. Every mutation re-derives its own authority
in `handover_verification_service`; a stale or over-optimistic flag can never widen what
a sender is allowed to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pickup.application.handover_verification_service import (
    CHALLENGE_ELIGIBLE_TASK_STATUSES,
)
from pickup.domain.entities import PickupTask
from pickup.domain.handover import CourierManifest, CourierManifestStatus
from pickup.domain.value_objects import ACTIVE_PICKUP_TASK_STATUSES
from pickup.ports.repository import HandoverUnitOfWork


class HandoverDiscoveryState(StrEnum):
    """Where the ceremony stands for the sender.

    The order is the happy path: the courier is verified, the parcel manifest is
    confirmed, and acceptance may then start custody.
    """

    AWAITING_COURIER_VERIFICATION = "AWAITING_COURIER_VERIFICATION"
    COURIER_VERIFIED = "COURIER_VERIFIED"
    AWAITING_MANIFEST_CONFIRMATION = "AWAITING_MANIFEST_CONFIRMATION"
    READY_FOR_ACCEPTANCE = "READY_FOR_ACCEPTANCE"
    COMPLETED = "COMPLETED"
    NOT_REQUIRED = "NOT_REQUIRED"


#: States in which the sender has nothing left to follow.
_CLOSED_STATES: frozenset[HandoverDiscoveryState] = frozenset(
    {HandoverDiscoveryState.COMPLETED, HandoverDiscoveryState.NOT_REQUIRED}
)


@dataclass(frozen=True, slots=True)
class HandoverDiscoveryView:
    """Privacy-minimised ceremony readiness for one shipment."""

    shipment_id: UUID
    pickup_task_id: UUID
    state: HandoverDiscoveryState
    verification_required: bool
    #: True while the ceremony is open for this shipment — the sender should keep
    #: following it. Not a claim that a button is enabled right now; the two
    #: ``can_*`` flags carry that.
    actionable: bool
    can_verify_courier: bool
    can_confirm_manifest: bool


class HandoverDiscoveryService:
    """Project the current ceremony state for one shipment's sender."""

    def __init__(
        self, unit_of_work: HandoverUnitOfWork, *, verification_required: bool
    ) -> None:
        self._uow = unit_of_work
        self._verification_required = verification_required

    def describe_for_shipment(self, shipment_id: UUID) -> HandoverDiscoveryView | None:
        """Return the ceremony projection, or None when no pickup task speaks for it."""
        task = self._resolve_task(shipment_id)
        if task is None:
            return None
        return self._project(task)

    # ------------------------------------------------------------------ resolution

    def _resolve_task(self, shipment_id: UUID) -> PickupTask | None:
        """The attempt the sender is looking at: the live one, else the one that took custody.

        A shipment may carry several attempts through recovery. Failed and superseded
        attempts are deliberately invisible here — they are recovery's story, not a
        ceremony the sender can still act on.
        """
        tasks = self._uow.pickup_tasks.list_tasks_for_shipment(shipment_id)
        if not tasks:
            return None

        live = [
            task
            for task in tasks
            if not task.is_accepted and task.status in ACTIVE_PICKUP_TASK_STATUSES
        ]
        if live:
            return max(live, key=_attempt_order)

        accepted = [task for task in tasks if task.is_accepted]
        if accepted:
            return max(accepted, key=_attempt_order)
        return None

    # ----------------------------------------------------------------- projection

    def _project(self, task: PickupTask) -> HandoverDiscoveryView:
        if task.is_accepted:
            return self._view(task, HandoverDiscoveryState.COMPLETED)

        if not self._verification_required:
            return self._view(task, HandoverDiscoveryState.NOT_REQUIRED)

        verified = any(
            challenge.is_live_verified
            for challenge in self._uow.challenges.list_open_for_task(task.pickup_task_id)
        )
        manifest = self._current_manifest(task.pickup_task_id)

        # The sender may only act while the attempt is still progressable. A task that
        # failed or was cancelled keeps its ceremony state visible but offers nothing.
        still_open = task.status in ACTIVE_PICKUP_TASK_STATUSES
        eligible = still_open and task.status in CHALLENGE_ELIGIBLE_TASK_STATUSES

        if not verified:
            # A challenge cannot exist before the driver acknowledges the assignment,
            # so offering the sender half then would promise a code nobody can produce.
            return self._view(
                task,
                HandoverDiscoveryState.AWAITING_COURIER_VERIFICATION,
                can_verify_courier=(
                    eligible and task.is_acknowledged and bool(task.assigned_driver_user_id)
                ),
            )

        if manifest is None:
            return self._view(task, HandoverDiscoveryState.COURIER_VERIFIED)

        if manifest.status is CourierManifestStatus.CONFIRMED:
            return self._view(task, HandoverDiscoveryState.READY_FOR_ACCEPTANCE)

        return self._view(
            task,
            HandoverDiscoveryState.AWAITING_MANIFEST_CONFIRMATION,
            can_confirm_manifest=eligible,
        )

    def _current_manifest(self, pickup_task_id: UUID) -> CourierManifest | None:
        manifests = self._uow.courier_manifests.list_open_for_task(pickup_task_id)
        if not manifests:
            return None
        return max(manifests, key=lambda item: item.submitted_at)

    def _view(
        self,
        task: PickupTask,
        state: HandoverDiscoveryState,
        *,
        can_verify_courier: bool = False,
        can_confirm_manifest: bool = False,
    ) -> HandoverDiscoveryView:
        return HandoverDiscoveryView(
            shipment_id=task.shipment_id,
            pickup_task_id=task.pickup_task_id,
            state=state,
            verification_required=self._verification_required,
            actionable=state not in _CLOSED_STATES,
            can_verify_courier=can_verify_courier,
            can_confirm_manifest=can_confirm_manifest,
        )


def _attempt_order(task: PickupTask) -> tuple[int, object]:
    return (task.attempt_number, task.created_at)
