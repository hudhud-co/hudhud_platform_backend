"""PostgreSQL acceptance unit of work with optimistic concurrency."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shipment.domain.entities import (
    AuditLogEntry,
    OrderIntent,
    PickupTaskSnapshot,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.errors import AcceptanceAlreadyRecorded, OptimisticConcurrencyConflict
from shipment.infrastructure.persistence.mappers import (
    acceptance_decision_from_audit,
    audit_log_from_row,
    audit_log_to_row,
    order_intent_from_row,
    order_intent_to_row,
    pickup_task_from_row,
    pickup_task_to_row,
    shipment_event_from_row,
    shipment_event_to_row,
    shipment_from_row,
    shipment_to_row,
)
from shipment.infrastructure.persistence.models import (
    AcceptanceAuditLogRow,
    OrderIntentRow,
    PickupTaskSnapshotRow,
    ShipmentEventRow,
    ShipmentRow,
)

_sync_event_loop_holder: dict[str, asyncio.AbstractEventLoop | None] = {"loop": None}


def _run_async(coro):  # noqa: ANN001, ANN201
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = _sync_event_loop_holder["loop"]
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            _sync_event_loop_holder["loop"] = loop
        return loop.run_until_complete(coro)
    msg = "SqlAlchemyAcceptanceUnitOfWork sync methods cannot run inside a running event loop"
    raise RuntimeError(msg)


@dataclass
class SqlAlchemyAcceptanceUnitOfWork:
    """Atomic acceptance persistence backed by PostgreSQL."""

    session_factory: async_sessionmaker[AsyncSession]
    _session: AsyncSession | None = field(default=None, init=False, repr=False)
    _shipment_versions: dict[UUID, int] = field(default_factory=dict, init=False, repr=False)
    _pickup_versions: dict[UUID, int] = field(default_factory=dict, init=False, repr=False)

    @property
    def shipments(self) -> _ShipmentRepo:
        return _ShipmentRepo(self)

    @property
    def pickup_tasks(self) -> _PickupTaskRepo:
        return _PickupTaskRepo(self)

    @property
    def shipment_events(self) -> _ShipmentEventRepo:
        return _ShipmentEventRepo(self)

    @property
    def audit_logs(self) -> _AuditLogRepo:
        return _AuditLogRepo(self)

    def begin(self) -> None:
        _run_async(self._begin())

    async def _begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self.session_factory()
        await self._session.begin()
        self._shipment_versions.clear()
        self._pickup_versions.clear()

    def commit(self) -> None:
        _run_async(self._commit())

    async def _commit(self) -> None:
        if self._session is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        finally:
            await self._session.close()
            self._session = None
            self._shipment_versions.clear()
            self._pickup_versions.clear()

    def rollback(self) -> None:
        _run_async(self._rollback())

    async def _rollback(self) -> None:
        if self._session is not None:
            await self._session.rollback()
            await self._session.close()
        self._session = None
        self._shipment_versions.clear()
        self._pickup_versions.clear()

    async def _session_or_factory(self) -> tuple[AsyncSession, bool]:
        if self._session is not None:
            return self._session, False
        session = self.session_factory()
        return session, True


class _ShipmentRepo:
    def __init__(self, store: SqlAlchemyAcceptanceUnitOfWork) -> None:
        self._store = store

    def save_order_intent(self, order_intent: OrderIntent) -> None:
        _run_async(self._save_order_intent(order_intent))

    def save_shipment(self, shipment: Shipment) -> None:
        _run_async(self._save_shipment(shipment))

    def get_shipment(self, shipment_id: UUID) -> Shipment | None:
        return _run_async(self._get_shipment(shipment_id))

    def get_order_intent(self, order_id: UUID) -> OrderIntent | None:
        return _run_async(self._get_order_intent(order_id))

    async def _save_order_intent(self, order_intent: OrderIntent) -> None:
        session, owned = await self._store._session_or_factory()
        try:
            session.add(order_intent_to_row(order_intent))
            if owned:
                await session.commit()
        except Exception:
            if owned:
                await session.rollback()
            raise
        finally:
            if owned:
                await session.close()

    async def _save_shipment(self, shipment: Shipment) -> None:
        session, owned = await self._store._session_or_factory()
        expected_version = self._store._shipment_versions.get(shipment.shipment_id)
        try:
            if expected_version is None:
                existing = await session.get(ShipmentRow, shipment.shipment_id)
                if existing is None:
                    session.add(shipment_to_row(shipment, version=1))
                else:
                    expected_version = existing.version
                    await self._update_shipment(session, shipment, expected_version)
            else:
                await self._update_shipment(session, shipment, expected_version)
            if owned:
                await session.commit()
        except Exception:
            if owned:
                await session.rollback()
            raise
        finally:
            if owned:
                await session.close()

    async def _update_shipment(
        self,
        session: AsyncSession,
        shipment: Shipment,
        expected_version: int,
    ) -> None:
        next_version = expected_version + 1
        row = shipment_to_row(shipment, version=next_version)
        result = await session.execute(
            update(ShipmentRow)
            .where(
                ShipmentRow.shipment_id == shipment.shipment_id,
                ShipmentRow.version == expected_version,
            )
            .values(
                current_status=row.current_status,
                accepted_at=row.accepted_at,
                sla_started_at=row.sla_started_at,
                current_custody_type=row.current_custody_type,
                current_custody_id=row.current_custody_id,
                version=next_version,
            )
        )
        if result.rowcount != 1:
            raise OptimisticConcurrencyConflict(
                entity_type="shipment",
                entity_id=str(shipment.shipment_id),
                expected_version=expected_version,
            )
        self._store._shipment_versions[shipment.shipment_id] = next_version

    async def _get_shipment(self, shipment_id: UUID) -> Shipment | None:
        session, owned = await self._store._session_or_factory()
        try:
            row = await session.get(ShipmentRow, shipment_id)
            if row is None:
                return None
            shipment, version = shipment_from_row(row)
            self._store._shipment_versions[shipment_id] = version
            return shipment
        finally:
            if owned:
                await session.close()

    async def _get_order_intent(self, order_id: UUID) -> OrderIntent | None:
        session, owned = await self._store._session_or_factory()
        try:
            row = await session.get(OrderIntentRow, order_id)
            return order_intent_from_row(row) if row is not None else None
        finally:
            if owned:
                await session.close()


class _PickupTaskRepo:
    def __init__(self, store: SqlAlchemyAcceptanceUnitOfWork) -> None:
        self._store = store

    def save_pickup_task(self, pickup_task: PickupTaskSnapshot) -> None:
        _run_async(self._save_pickup_task(pickup_task))

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTaskSnapshot | None:
        return _run_async(self._get_pickup_task(pickup_task_id))

    def get_pickup_task_for_shipment(self, shipment_id: UUID) -> PickupTaskSnapshot | None:
        return _run_async(self._get_pickup_task_for_shipment(shipment_id))

    async def _save_pickup_task(self, pickup_task: PickupTaskSnapshot) -> None:
        session, owned = await self._store._session_or_factory()
        expected_version = self._store._pickup_versions.get(pickup_task.pickup_task_id)
        try:
            if expected_version is None:
                existing = await session.get(PickupTaskSnapshotRow, pickup_task.pickup_task_id)
                if existing is None:
                    session.add(pickup_task_to_row(pickup_task, version=1))
                else:
                    expected_version = existing.version
                    await self._update_pickup_task(session, pickup_task, expected_version)
            else:
                await self._update_pickup_task(session, pickup_task, expected_version)
            if owned:
                await session.commit()
        except Exception:
            if owned:
                await session.rollback()
            raise
        finally:
            if owned:
                await session.close()

    async def _update_pickup_task(
        self,
        session: AsyncSession,
        pickup_task: PickupTaskSnapshot,
        expected_version: int,
    ) -> None:
        next_version = expected_version + 1
        row = pickup_task_to_row(pickup_task, version=next_version)
        result = await session.execute(
            update(PickupTaskSnapshotRow)
            .where(
                PickupTaskSnapshotRow.pickup_task_id == pickup_task.pickup_task_id,
                PickupTaskSnapshotRow.version == expected_version,
            )
            .values(
                status=row.status,
                assigned_driver_user_id=row.assigned_driver_user_id,
                assigned_batch_id=row.assigned_batch_id,
                has_pickup_condition_proof=row.has_pickup_condition_proof,
                acceptance_state=row.acceptance_state,
                version=next_version,
            )
        )
        if result.rowcount != 1:
            raise OptimisticConcurrencyConflict(
                entity_type="pickup_task",
                entity_id=str(pickup_task.pickup_task_id),
                expected_version=expected_version,
            )
        self._store._pickup_versions[pickup_task.pickup_task_id] = next_version

    async def _get_pickup_task(self, pickup_task_id: UUID) -> PickupTaskSnapshot | None:
        session, owned = await self._store._session_or_factory()
        try:
            row = await session.get(PickupTaskSnapshotRow, pickup_task_id)
            if row is None:
                return None
            pickup_task, version = pickup_task_from_row(row)
            self._store._pickup_versions[pickup_task_id] = version
            return pickup_task
        finally:
            if owned:
                await session.close()

    async def _get_pickup_task_for_shipment(self, shipment_id: UUID) -> PickupTaskSnapshot | None:
        session, owned = await self._store._session_or_factory()
        try:
            row = await session.execute(
                select(PickupTaskSnapshotRow).where(
                    PickupTaskSnapshotRow.shipment_id == shipment_id
                )
            )
            found = row.scalar_one_or_none()
            if found is None:
                return None
            pickup_task, version = pickup_task_from_row(found)
            self._store._pickup_versions[pickup_task.pickup_task_id] = version
            return pickup_task
        finally:
            if owned:
                await session.close()


class _ShipmentEventRepo:
    def __init__(self, store: SqlAlchemyAcceptanceUnitOfWork) -> None:
        self._store = store

    def append_event(self, event: ShipmentEvent) -> None:
        _run_async(self._append_event(event))

    def list_events_for_shipment(self, shipment_id: UUID) -> tuple[ShipmentEvent, ...]:
        return _run_async(self._list_events_for_shipment(shipment_id))

    async def _append_event(self, event: ShipmentEvent) -> None:
        session, owned = await self._store._session_or_factory()
        try:
            session.add(shipment_event_to_row(event))
            if owned:
                await session.commit()
        except IntegrityError as exc:
            if owned:
                await session.rollback()
            raise AcceptanceAlreadyRecorded(str(event.shipment_id)) from exc
        except Exception:
            if owned:
                await session.rollback()
            raise
        finally:
            if owned:
                await session.close()

    async def _list_events_for_shipment(self, shipment_id: UUID) -> tuple[ShipmentEvent, ...]:
        session, owned = await self._store._session_or_factory()
        try:
            rows = await session.execute(
                select(ShipmentEventRow)
                .where(ShipmentEventRow.shipment_id == shipment_id)
                .order_by(ShipmentEventRow.occurred_at)
            )
            return tuple(shipment_event_from_row(row) for row in rows.scalars())
        finally:
            if owned:
                await session.close()


class _AuditLogRepo:
    def __init__(self, store: SqlAlchemyAcceptanceUnitOfWork) -> None:
        self._store = store

    def append_entry(self, entry: AuditLogEntry) -> None:
        _run_async(self._append_entry(entry))

    def list_entries_for_entity(
        self, entity_type: str, entity_id: str
    ) -> tuple[AuditLogEntry, ...]:
        return _run_async(self._list_entries_for_entity(entity_type, entity_id))

    async def _append_entry(self, entry: AuditLogEntry) -> None:
        session, owned = await self._store._session_or_factory()
        try:
            session.add(audit_log_to_row(entry))
            if entry.action == "SHIPMENT_ACCEPTANCE_SCAN":
                session.add(acceptance_decision_from_audit(entry))
            if owned:
                await session.commit()
        except IntegrityError as exc:
            if owned:
                await session.rollback()
            raise AcceptanceAlreadyRecorded(entry.entity_id) from exc
        except Exception:
            if owned:
                await session.rollback()
            raise
        finally:
            if owned:
                await session.close()

    async def _list_entries_for_entity(
        self, entity_type: str, entity_id: str
    ) -> tuple[AuditLogEntry, ...]:
        session, owned = await self._store._session_or_factory()
        try:
            rows = await session.execute(
                select(AcceptanceAuditLogRow)
                .where(
                    AcceptanceAuditLogRow.entity_type == entity_type,
                    AcceptanceAuditLogRow.entity_id == entity_id,
                )
                .order_by(AcceptanceAuditLogRow.occurred_at)
            )
            return tuple(audit_log_from_row(row) for row in rows.scalars())
        finally:
            if owned:
                await session.close()
