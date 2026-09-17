"""Driver→hub handover manifest lifecycle and custody release.

Pickup owns the driver-side manifest and the physical receipt record. The canonical
Shipment custody change is *not* written here: each released parcel enqueues
`pickup.fact.handover_completed` on the service-owned transactional outbox, and Shipment
applies the custody move (ADR-0003, ADR-0008).

A parcel that is listed but never produced at the hub stays in pickup-driver custody and
publishes nothing — that is what keeps a missing parcel attached to its driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pickup.application.handover_fact_mapper import (
    RELEASING_DISCREPANCY_REASONS,
    HandoverOutcome,
    build_handover_completed_envelope,
)
from pickup.application.history import record_history
from pickup.domain.entities import OutboxRecord, PickupTask
from pickup.domain.errors import (
    ActingDriverMismatch,
    HandoverManifestEmpty,
    HandoverManifestItemNotFound,
    HandoverManifestNotArrived,
    HandoverManifestNotFound,
    HandoverManifestNotMutable,
    HubScopeNotAuthorized,
    PickupTaskNotFound,
    ShipmentAlreadyOnActiveManifest,
    ShipmentNotInDriverCustody,
)
from pickup.domain.handover import (
    HandoverDiscrepancyReason,
    HandoverManifest,
    HandoverManifestItem,
    HandoverManifestItemStatus,
    HandoverManifestStatus,
)
from pickup.domain.value_objects import EvidenceMediaRef, OutboxStatus
from pickup.ports.authorization import PickupActor
from pickup.ports.repository import HandoverUnitOfWork

DEFAULT_OUTBOX_MAX_ATTEMPTS = 5
MANIFEST_CODE_PREFIX = "PHM"


@dataclass(frozen=True, slots=True)
class CreateHandoverManifestCommand:
    driver_user_id: str
    hub_id: UUID
    pickup_task_ids: tuple[UUID, ...]
    occurred_at: datetime
    notes: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestLifecycleCommand:
    manifest_id: UUID
    driver_user_id: str
    occurred_at: datetime
    reason: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecordHubReceiptCommand:
    manifest_id: UUID
    shipment_id: UUID
    receiving_hub_id: UUID
    occurred_at: datetime
    discrepancy_reason: HandoverDiscrepancyReason | None = None
    notes: str | None = None
    media_refs: tuple[EvidenceMediaRef, ...] = ()
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    tenant_id: UUID | None = None
    traceparent: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CloseManifestCommand:
    manifest_id: UUID
    occurred_at: datetime
    missing_shipment_ids: tuple[UUID, ...] = ()
    notes: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class HandoverManifestView:
    manifest: HandoverManifest
    items: tuple[HandoverManifestItem, ...]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class HubReceiptResult:
    manifest: HandoverManifest
    item: HandoverManifestItem
    outbox_record: OutboxRecord | None
    custody_released: bool
    replayed: bool = False


class HubHandoverService:
    """Create, move, and close the driver's hub handover manifest."""

    def __init__(self, unit_of_work: HandoverUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------------------- driver side

    def create_manifest(
        self, command: CreateHandoverManifestCommand, *, actor: PickupActor
    ) -> HandoverManifestView:
        if not command.pickup_task_ids:
            raise HandoverManifestEmpty()
        self._uow.begin()
        try:
            manifest_id = uuid4()
            items: list[HandoverManifestItem] = []
            for pickup_task_id in dict.fromkeys(command.pickup_task_ids):
                task = self._load_task(pickup_task_id)
                if task.assigned_driver_user_id != command.driver_user_id:
                    raise ActingDriverMismatch(
                        pickup_task_id=str(pickup_task_id),
                        acting_driver_user_id=command.driver_user_id,
                    )
                if not task.is_accepted or task.is_terminal:
                    raise ShipmentNotInDriverCustody(pickup_task_id=str(pickup_task_id))
                existing = self._uow.handover_manifests.find_custody_item_for_shipment(
                    task.shipment_id
                )
                if existing is not None and existing.releases_custody:
                    raise ShipmentNotInDriverCustody(pickup_task_id=str(pickup_task_id))
                active = self._uow.handover_manifests.find_active_item_for_shipment(
                    task.shipment_id
                )
                if active is not None:
                    raise ShipmentAlreadyOnActiveManifest(
                        shipment_id=str(task.shipment_id),
                        manifest_id=str(active.manifest_id),
                    )
                items.append(
                    HandoverManifestItem(
                        item_id=uuid4(),
                        manifest_id=manifest_id,
                        pickup_task_id=task.pickup_task_id,
                        shipment_id=task.shipment_id,
                        status=HandoverManifestItemStatus.EXPECTED,
                        added_at=command.occurred_at,
                    )
                )

            manifest = HandoverManifest(
                manifest_id=manifest_id,
                manifest_code=_manifest_code(manifest_id),
                driver_user_id=command.driver_user_id,
                hub_id=command.hub_id,
                status=HandoverManifestStatus.READY,
                expected_count=len(items),
                received_count=0,
                missing_count=0,
                discrepancy_count=0,
                created_at=command.occurred_at,
                ready_at=command.occurred_at,
                notes=command.notes,
                version=1,
            )
            self._uow.handover_manifests.save_manifest(manifest)
            for item in items:
                self._uow.handover_manifests.save_item(item)
                record_history(
                    self._uow.task_history,
                    pickup_task_id=item.pickup_task_id,
                    action="handover_manifest_listed",
                    actor=actor,
                    previous_status=None,
                    new_status=HandoverManifestItemStatus.EXPECTED.value,
                    occurred_at=command.occurred_at,
                    request_id=command.request_id,
                    details={
                        "manifest_id": str(manifest_id),
                        "manifest_code": manifest.manifest_code,
                        "hub_id": str(command.hub_id),
                    },
                )
            view = HandoverManifestView(manifest=manifest, items=tuple(items))
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    def arrive_at_hub(
        self, command: ManifestLifecycleCommand, *, actor: PickupActor
    ) -> HandoverManifestView:
        self._uow.begin()
        try:
            manifest = self._load_owned_manifest(command.manifest_id, command.driver_user_id)
            if manifest.status is HandoverManifestStatus.ARRIVED_AT_HUB:
                view = self._view(manifest, replayed=True)
                self._uow.commit()
                return view
            if manifest.status is not HandoverManifestStatus.READY:
                raise HandoverManifestNotMutable(
                    manifest_id=str(manifest.manifest_id),
                    status=manifest.status.value,
                )
            manifest.status = HandoverManifestStatus.ARRIVED_AT_HUB
            manifest.arrived_at_hub_at = command.occurred_at
            manifest.version += 1
            self._uow.handover_manifests.save_manifest(manifest)
            self._record_manifest_history(
                manifest,
                action="handover_manifest_arrived",
                actor=actor,
                occurred_at=command.occurred_at,
                request_id=command.request_id,
            )
            view = self._view(manifest)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    def cancel_manifest(
        self, command: ManifestLifecycleCommand, *, actor: PickupActor
    ) -> HandoverManifestView:
        self._uow.begin()
        try:
            manifest = self._load_owned_manifest(command.manifest_id, command.driver_user_id)
            if manifest.status is HandoverManifestStatus.CANCELLED:
                view = self._view(manifest, replayed=True)
                self._uow.commit()
                return view
            if manifest.is_terminal:
                raise HandoverManifestNotMutable(
                    manifest_id=str(manifest.manifest_id),
                    status=manifest.status.value,
                )
            items = self._uow.handover_manifests.list_items(manifest.manifest_id)
            if any(item.releases_custody for item in items):
                # A hub already took physical custody of part of this manifest.
                raise HandoverManifestNotMutable(
                    manifest_id=str(manifest.manifest_id),
                    status=manifest.status.value,
                )
            manifest.status = HandoverManifestStatus.CANCELLED
            manifest.cancelled_at = command.occurred_at
            manifest.notes = command.reason or manifest.notes
            manifest.version += 1
            self._uow.handover_manifests.save_manifest(manifest)
            self._record_manifest_history(
                manifest,
                action="handover_manifest_cancelled",
                actor=actor,
                occurred_at=command.occurred_at,
                request_id=command.request_id,
                details={"reason": command.reason},
            )
            view = self._view(manifest)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    # ---------------------------------------------------------------- hub side

    def record_hub_receipt(
        self, command: RecordHubReceiptCommand, *, actor: PickupActor
    ) -> HubReceiptResult:
        """Record a scoped hub receipt and enqueue the custody-releasing fact."""
        self._uow.begin()
        try:
            manifest = self._load_manifest(command.manifest_id)
            if not actor.scoped_to_hub(command.receiving_hub_id):
                raise HubScopeNotAuthorized(hub_id=str(command.receiving_hub_id))
            if actor.actor_id == manifest.driver_user_id:
                # A driver may never release its own custody at the hub.
                raise HubScopeNotAuthorized(hub_id=str(command.receiving_hub_id))
            item = self._load_item(manifest.manifest_id, command.shipment_id)
            if item.releases_custody:
                # Concurrent scans converge on one receipt; the second is a replay.
                # This is checked before the manifest-state guards because the first
                # scan may already have completed the manifest.
                result = HubReceiptResult(
                    manifest=manifest,
                    item=item,
                    outbox_record=None,
                    custody_released=True,
                    replayed=True,
                )
                self._uow.commit()
                return result
            if manifest.status is HandoverManifestStatus.READY:
                raise HandoverManifestNotArrived(
                    manifest_id=str(manifest.manifest_id),
                    status=manifest.status.value,
                )
            if manifest.is_terminal:
                raise HandoverManifestNotMutable(
                    manifest_id=str(manifest.manifest_id),
                    status=manifest.status.value,
                )

            discrepancy = _resolve_discrepancy(
                planned_hub_id=manifest.hub_id,
                receiving_hub_id=command.receiving_hub_id,
                requested=command.discrepancy_reason,
            )
            task = self._load_task(item.pickup_task_id)
            outbox = self._enqueue_handover_fact(
                task=task,
                manifest=manifest,
                item=item,
                command=command,
                discrepancy=discrepancy,
                receiving_actor_id=actor.actor_id,
            )

            previous_status = item.status.value
            item.status = (
                HandoverManifestItemStatus.DISCREPANCY
                if discrepancy is not None
                else HandoverManifestItemStatus.RECEIVED
            )
            item.received_at = command.occurred_at
            item.received_by_user_id = actor.actor_id
            item.received_hub_id = command.receiving_hub_id
            item.discrepancy_reason = discrepancy
            item.notes = command.notes
            self._uow.handover_manifests.save_item(item)

            if manifest.status is HandoverManifestStatus.ARRIVED_AT_HUB:
                manifest.status = HandoverManifestStatus.IN_PROGRESS
            manifest = self._recount(manifest)
            manifest = self._maybe_complete(manifest, completed_at=command.occurred_at)
            self._uow.handover_manifests.save_manifest(manifest)

            record_history(
                self._uow.task_history,
                pickup_task_id=item.pickup_task_id,
                action="handover_receipt_recorded",
                actor=actor,
                previous_status=previous_status,
                new_status=item.status.value,
                occurred_at=command.occurred_at,
                request_id=command.request_id,
                details={
                    "manifest_id": str(manifest.manifest_id),
                    "receiving_hub_id": str(command.receiving_hub_id),
                    "planned_hub_id": str(manifest.hub_id),
                    "discrepancy_reason": discrepancy.value if discrepancy else None,
                    "event_id": str(outbox.event_id),
                },
            )
            result = HubReceiptResult(
                manifest=manifest,
                item=item,
                outbox_record=outbox,
                custody_released=True,
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def close_manifest(
        self, command: CloseManifestCommand, *, actor: PickupActor
    ) -> HandoverManifestView:
        """Close an arrived manifest, naming every parcel the driver did not produce."""
        self._uow.begin()
        try:
            manifest = self._load_manifest(command.manifest_id)
            if not actor.scoped_to_hub(manifest.hub_id):
                raise HubScopeNotAuthorized(hub_id=str(manifest.hub_id))
            if manifest.is_terminal:
                view = self._view(manifest, replayed=True)
                self._uow.commit()
                return view
            if manifest.status is HandoverManifestStatus.READY:
                raise HandoverManifestNotArrived(
                    manifest_id=str(manifest.manifest_id),
                    status=manifest.status.value,
                )

            missing = set(command.missing_shipment_ids)
            items = self._uow.handover_manifests.list_items(manifest.manifest_id)
            for item in items:
                if item.releases_custody:
                    continue
                if item.shipment_id not in missing:
                    raise HandoverManifestItemNotFound(
                        manifest_id=str(manifest.manifest_id),
                        shipment_id=str(item.shipment_id),
                    )
                previous_status = item.status.value
                item.status = HandoverManifestItemStatus.MISSING
                item.discrepancy_reason = HandoverDiscrepancyReason.MISSING_FROM_DRIVER
                item.notes = command.notes
                self._uow.handover_manifests.save_item(item)
                record_history(
                    self._uow.task_history,
                    pickup_task_id=item.pickup_task_id,
                    action="handover_item_missing",
                    actor=actor,
                    previous_status=previous_status,
                    new_status=item.status.value,
                    occurred_at=command.occurred_at,
                    request_id=command.request_id,
                    details={
                        "manifest_id": str(manifest.manifest_id),
                        "custody_retained_by": manifest.driver_user_id,
                    },
                )

            manifest = self._recount(manifest)
            manifest.status = (
                HandoverManifestStatus.COMPLETED
                if manifest.missing_count == 0 and manifest.discrepancy_count == 0
                else HandoverManifestStatus.COMPLETED_WITH_DISCREPANCIES
            )
            manifest.completed_at = command.occurred_at
            manifest.version += 1
            self._uow.handover_manifests.save_manifest(manifest)
            self._record_manifest_history(
                manifest,
                action="handover_manifest_closed",
                actor=actor,
                occurred_at=command.occurred_at,
                request_id=command.request_id,
                details={
                    "missing_count": manifest.missing_count,
                    "discrepancy_count": manifest.discrepancy_count,
                },
            )
            view = self._view(manifest)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return view

    # -------------------------------------------------------------------- reads

    def read_manifest(self, manifest_id: UUID) -> HandoverManifestView:
        """Read-only manifest projection — no transaction side effects."""
        return self._view(self._load_manifest(manifest_id))

    def list_items(self, manifest_id: UUID) -> tuple[HandoverManifestItem, ...]:
        return self._uow.handover_manifests.list_items(manifest_id)

    # ------------------------------------------------------------------ helpers

    def _enqueue_handover_fact(
        self,
        *,
        task: PickupTask,
        manifest: HandoverManifest,
        item: HandoverManifestItem,
        command: RecordHubReceiptCommand,
        discrepancy: HandoverDiscrepancyReason | None,
        receiving_actor_id: str,
    ) -> OutboxRecord:
        outcome = (
            HandoverOutcome.RECEIVED_WITH_DISCREPANCY
            if discrepancy is not None
            else HandoverOutcome.RECEIVED
        )
        next_version = task.version + 1
        event_id = uuid4()
        payload_json, subject = build_handover_completed_envelope(
            pickup_task_id=task.pickup_task_id,
            shipment_id=item.shipment_id,
            handover_manifest_id=manifest.manifest_id,
            receiving_hub_id=command.receiving_hub_id,
            outcome=outcome,
            released_at=_as_utc(command.occurred_at),
            releasing_driver_user_id=manifest.driver_user_id,
            receiving_actor_id=receiving_actor_id,
            aggregate_version=next_version,
            event_id=event_id,
            correlation_id=command.correlation_id or uuid4(),
            discrepancy_reason=discrepancy,
            media_refs=command.media_refs,
            causation_id=command.causation_id,
            tenant_id=command.tenant_id,
            traceparent=command.traceparent,
        )
        task.version = next_version
        self._uow.pickup_tasks.save_pickup_task(task)

        now = datetime.now(tz=UTC)
        record = OutboxRecord(
            id=uuid4(),
            event_id=event_id,
            subject=subject,
            event_type=payload_json["event_type"],
            event_version=int(payload_json["event_version"]),
            aggregate_id=task.pickup_task_id,
            aggregate_version=next_version,
            payload_json=payload_json,
            status=OutboxStatus.PENDING,
            attempt_count=0,
            max_attempts=DEFAULT_OUTBOX_MAX_ATTEMPTS,
            next_attempt_at=now,
            processing_owner=None,
            processing_until=None,
            published_at=None,
            last_error_code=None,
            last_error_message=None,
            created_at=now,
        )
        self._uow.outbox.insert(record)
        return record

    def _recount(self, manifest: HandoverManifest) -> HandoverManifest:
        items = self._uow.handover_manifests.list_items(manifest.manifest_id)
        manifest.expected_count = len(items)
        manifest.received_count = sum(
            item.status is HandoverManifestItemStatus.RECEIVED for item in items
        )
        manifest.missing_count = sum(
            item.status is HandoverManifestItemStatus.MISSING for item in items
        )
        manifest.discrepancy_count = sum(
            item.status is HandoverManifestItemStatus.DISCREPANCY for item in items
        )
        manifest.version += 1
        return manifest

    def _maybe_complete(
        self, manifest: HandoverManifest, *, completed_at: datetime
    ) -> HandoverManifest:
        items = self._uow.handover_manifests.list_items(manifest.manifest_id)
        if not items:
            return manifest
        if all(item.status is HandoverManifestItemStatus.RECEIVED for item in items):
            manifest.status = HandoverManifestStatus.COMPLETED
            manifest.completed_at = completed_at
        elif all(
            item.status
            in (
                HandoverManifestItemStatus.RECEIVED,
                HandoverManifestItemStatus.DISCREPANCY,
            )
            for item in items
        ):
            manifest.status = HandoverManifestStatus.COMPLETED_WITH_DISCREPANCIES
            manifest.completed_at = completed_at
        return manifest

    def _load_manifest(self, manifest_id: UUID) -> HandoverManifest:
        manifest = self._uow.handover_manifests.get_manifest(manifest_id)
        if manifest is None:
            raise HandoverManifestNotFound(manifest_id=str(manifest_id))
        return manifest

    def _load_owned_manifest(self, manifest_id: UUID, driver_user_id: str) -> HandoverManifest:
        manifest = self._load_manifest(manifest_id)
        if manifest.driver_user_id != driver_user_id:
            raise HandoverManifestNotFound(manifest_id=str(manifest_id))
        return manifest

    def _load_item(self, manifest_id: UUID, shipment_id: UUID) -> HandoverManifestItem:
        for item in self._uow.handover_manifests.list_items(manifest_id):
            if item.shipment_id == shipment_id:
                return item
        raise HandoverManifestItemNotFound(
            manifest_id=str(manifest_id),
            shipment_id=str(shipment_id),
        )

    def _load_task(self, pickup_task_id: UUID) -> PickupTask:
        task = self._uow.pickup_tasks.get_pickup_task(pickup_task_id)
        if task is None:
            raise PickupTaskNotFound(str(pickup_task_id))
        return task

    def _view(
        self, manifest: HandoverManifest, *, replayed: bool = False
    ) -> HandoverManifestView:
        return HandoverManifestView(
            manifest=manifest,
            items=self._uow.handover_manifests.list_items(manifest.manifest_id),
            replayed=replayed,
        )

    def _record_manifest_history(
        self,
        manifest: HandoverManifest,
        *,
        action: str,
        actor: PickupActor,
        occurred_at: datetime,
        request_id: str | None,
        details: dict[str, object] | None = None,
    ) -> None:
        for item in self._uow.handover_manifests.list_items(manifest.manifest_id):
            record_history(
                self._uow.task_history,
                pickup_task_id=item.pickup_task_id,
                action=action,
                actor=actor,
                previous_status=None,
                new_status=manifest.status.value,
                occurred_at=occurred_at,
                request_id=request_id,
                details={
                    "manifest_id": str(manifest.manifest_id),
                    "manifest_code": manifest.manifest_code,
                    **(details or {}),
                },
            )


def _resolve_discrepancy(
    *,
    planned_hub_id: UUID,
    receiving_hub_id: UUID,
    requested: HandoverDiscrepancyReason | None,
) -> HandoverDiscrepancyReason | None:
    """Receiving at an unplanned hub is always a discrepancy, declared or not."""
    if requested is HandoverDiscrepancyReason.MISSING_FROM_DRIVER:
        # A parcel physically present cannot be missing.
        msg = "MISSING_FROM_DRIVER is not a receipt discrepancy"
        raise ValueError(msg)
    if receiving_hub_id != planned_hub_id:
        return requested or HandoverDiscrepancyReason.WRONG_HUB_RECEIVED
    if requested is not None and requested in RELEASING_DISCREPANCY_REASONS:
        return requested
    return None


def _manifest_code(manifest_id: UUID) -> str:
    return f"{MANIFEST_CODE_PREFIX}-{manifest_id.hex[:16].upper()}"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
