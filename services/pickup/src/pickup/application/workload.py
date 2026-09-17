"""Operational blockers that keep a driver bound to work in progress.

A driver may not go on break or end a session while the platform still believes the
driver physically holds parcels, owes a hub handover, or has offline captures the
server could not apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pickup.domain.entities import PickupTask
from pickup.domain.offline import OfflineEventStatus
from pickup.domain.value_objects import ACTIVE_PICKUP_TASK_STATUSES
from pickup.domain.workforce import WorkSessionBlocker
from pickup.ports.repository import (
    HandoverManifestRepository,
    OfflineEventRepository,
    PickupTaskRepository,
)


@dataclass(frozen=True, slots=True)
class DriverWorkload:
    """Snapshot of what still ties a driver to the field."""

    open_custody_shipment_ids: tuple[UUID, ...]
    active_handover_manifest_ids: tuple[UUID, ...]
    active_task_ids: tuple[UUID, ...]
    unsynced_offline_operation_ids: tuple[UUID, ...]

    def end_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.open_custody_shipment_ids:
            blockers.append(WorkSessionBlocker.OPEN_PICKUP_CUSTODY.value)
        if self.active_handover_manifest_ids:
            blockers.append(WorkSessionBlocker.ACTIVE_HANDOVER_MANIFEST.value)
        if self.active_task_ids:
            blockers.append(WorkSessionBlocker.ACTIVE_ASSIGNED_TASK.value)
        if self.unsynced_offline_operation_ids:
            blockers.append(WorkSessionBlocker.UNSYNCED_OFFLINE_WORK.value)
        return tuple(sorted(blockers))

    def pause_blockers(self) -> tuple[str, ...]:
        """Pausing is a break, not a release: only unreconciled offline work blocks it."""
        if self.unsynced_offline_operation_ids:
            return (WorkSessionBlocker.UNSYNCED_OFFLINE_WORK.value,)
        return ()


def collect_driver_workload(
    *,
    driver_user_id: str,
    pickup_tasks: PickupTaskRepository,
    handover_manifests: HandoverManifestRepository,
    offline_events: OfflineEventRepository,
) -> DriverWorkload:
    """Compute blockers from Pickup-owned state only — no cross-service reads."""
    tasks = pickup_tasks.list_tasks_for_driver(driver_user_id)
    open_custody: list[UUID] = []
    active_tasks: list[UUID] = []
    for task in tasks:
        if _still_in_driver_custody(task, handover_manifests):
            open_custody.append(task.shipment_id)
        if _is_active_commitment(task):
            active_tasks.append(task.pickup_task_id)

    manifests = handover_manifests.list_active_for_driver(driver_user_id)
    unsynced = tuple(
        event.operation_id
        for event in offline_events.list_for_driver(driver_user_id)
        if event.status is OfflineEventStatus.RECONCILIATION_REQUIRED
    )
    return DriverWorkload(
        open_custody_shipment_ids=tuple(sorted(set(open_custody), key=str)),
        active_handover_manifest_ids=tuple(
            sorted((manifest.manifest_id for manifest in manifests), key=str)
        ),
        active_task_ids=tuple(sorted(set(active_tasks), key=str)),
        unsynced_offline_operation_ids=tuple(sorted(set(unsynced), key=str)),
    )


def _still_in_driver_custody(
    task: PickupTask,
    handover_manifests: HandoverManifestRepository,
) -> bool:
    """Custody starts at acceptance and ends only when a hub physically receives."""
    if not task.is_accepted or task.is_terminal:
        return False
    item = handover_manifests.find_custody_item_for_shipment(task.shipment_id)
    if item is None:
        return True
    # A parcel listed but not produced at the hub stays with the driver.
    return not item.releases_custody


def _is_active_commitment(task: PickupTask) -> bool:
    return (
        task.is_acknowledged
        and not task.is_accepted
        and task.status in ACTIVE_PICKUP_TASK_STATUSES
    )
