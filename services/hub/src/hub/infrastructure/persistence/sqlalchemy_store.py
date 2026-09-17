"""PostgreSQL unit of work for the Hub service.

Writes are staged during the transaction and flushed on commit with a version-conditional
UPDATE, so a lost update surfaces as `StaleHubRecord` rather than silently winning.
Seal checks and vehicle positions are append-only and are simply inserted: both are
evidence, and evidence is not edited.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from hub.domain.entities import (
    Consignment,
    DropOff,
    Hub,
    Linehaul,
    ParcelPresence,
    SealCheck,
    SecuritySeal,
    VehiclePosition,
)
from hub.domain.errors import StaleHubRecord
from hub.domain.messaging import (
    InboxRecord,
    InboxStatus,
    OutboxRecord,
    OutboxStatus,
)
from hub.domain.value_objects import (
    DROP_OFF_TRANSITIONS,
    ConsignmentStatus,
    CutOffTime,
    DropOffStatus,
    GeoPoint,
    HoldReason,
    LinehaulStatus,
    ParcelDisposition,
    ParcelPresenceStatus,
    PositionSource,
    RoutingDecision,
    SealCheckOutcome,
    Urgency,
)
from hub.infrastructure.persistence.models import (
    ConsignmentRow,
    DropOffRow,
    HubRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    LinehaulRow,
    ParcelPresenceRow,
    SealCheckRow,
    VehiclePositionRow,
)

_OPEN_DROP_OFF_VALUES = tuple(
    status.value for status, onward in DROP_OFF_TRANSITIONS.items() if onward
)

_STAGES = (
    "hubs",
    "drop_offs",
    "presences",
    "consignments",
    "seal_checks",
    "linehauls",
    "positions",
    "outbox",
    "inbox",
)


class SqlAlchemyHubUnitOfWork:
    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session: SaSession | None = None
        self._pending: dict[str, dict] | None = None
        self._hubs = _HubRepo(self)
        self._drop_offs = _DropOffRepo(self)
        self._presences = _PresenceRepo(self)
        self._consignments = _ConsignmentRepo(self)
        self._seal_checks = _SealCheckRepo(self)
        self._linehauls = _LinehaulRepo(self)
        self._positions = _PositionRepo(self)
        self._outbox = _OutboxRepo(self)
        self._inbox = _InboxRepo(self)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {name: {} for name in _STAGES}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            self._flush(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
            self._pending = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending = None

    def _flush(self, session: SaSession) -> None:
        pending = self._pending
        assert pending is not None
        for entity, previous in pending["hubs"].values():
            _versioned(
                session,
                HubRow,
                HubRow.hub_id == entity.hub_id,
                previous=previous,
                values=_hub_values(entity),
                new_row=lambda e=entity: HubRow(hub_id=e.hub_id, **_hub_values(e)),
            )
        for entity, previous in pending["drop_offs"].values():
            _versioned(
                session,
                DropOffRow,
                DropOffRow.drop_off_id == entity.drop_off_id,
                previous=previous,
                values=_drop_off_values(entity),
                new_row=lambda e=entity: DropOffRow(
                    drop_off_id=e.drop_off_id, **_drop_off_values(e)
                ),
            )
        for entity, previous in pending["presences"].values():
            _versioned(
                session,
                ParcelPresenceRow,
                ParcelPresenceRow.presence_id == entity.presence_id,
                previous=previous,
                values=_presence_values(entity),
                new_row=lambda e=entity: ParcelPresenceRow(
                    presence_id=e.presence_id, **_presence_values(e)
                ),
            )
        for entity, previous in pending["consignments"].values():
            _versioned(
                session,
                ConsignmentRow,
                ConsignmentRow.consignment_id == entity.consignment_id,
                previous=previous,
                values=_consignment_values(entity),
                new_row=lambda e=entity: ConsignmentRow(
                    consignment_id=e.consignment_id, **_consignment_values(e)
                ),
            )
        for entity, previous in pending["linehauls"].values():
            _versioned(
                session,
                LinehaulRow,
                LinehaulRow.linehaul_id == entity.linehaul_id,
                previous=previous,
                values=_linehaul_values(entity),
                new_row=lambda e=entity: LinehaulRow(
                    linehaul_id=e.linehaul_id, **_linehaul_values(e)
                ),
            )
        for entity in pending["seal_checks"].values():
            session.add(SealCheckRow(**_seal_check_values(entity)))
        for entity in pending["positions"].values():
            session.add(VehiclePositionRow(**_position_values(entity)))
        for record in pending["outbox"].values():
            session.add(IntegrationOutboxRow(**_outbox_values(record)))
        for record, is_new in pending["inbox"].values():
            if is_new:
                session.add(IntegrationInboxRow(**_inbox_values(record)))
            else:
                session.execute(
                    update(IntegrationInboxRow)
                    .where(
                        IntegrationInboxRow.consumer_name == record.consumer_name,
                        IntegrationInboxRow.event_id == record.event_id,
                    )
                    .values(**_inbox_update_values(record))
                )

    def _require(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

    @property
    def hubs(self) -> _HubRepo:
        return self._hubs

    @property
    def drop_offs(self) -> _DropOffRepo:
        return self._drop_offs

    @property
    def presences(self) -> _PresenceRepo:
        return self._presences

    @property
    def consignments(self) -> _ConsignmentRepo:
        return self._consignments

    @property
    def seal_checks(self) -> _SealCheckRepo:
        return self._seal_checks

    @property
    def linehauls(self) -> _LinehaulRepo:
        return self._linehauls

    @property
    def positions(self) -> _PositionRepo:
        return self._positions

    @property
    def outbox(self) -> _OutboxRepo:
        return self._outbox

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleHubRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _Repo:
    def __init__(self, uow: SqlAlchemyHubUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _HubRepo(_Repo):
    def save(self, hub: Hub) -> None:
        _stage(self._stage_for("hubs"), hub.hub_id, hub, hub.version)

    def get(self, hub_id: UUID) -> Hub | None:
        staged = self._stage_for("hubs").get(hub_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(HubRow, hub_id)
        return _hub_entity(row) if row is not None else None

    def find_by_code(self, code: str) -> Hub | None:
        row = (
            self._session()
            .execute(select(HubRow).where(HubRow.code == code))
            .scalars()
            .first()
        )
        return _hub_entity(row) if row is not None else None

    def list_all(self) -> tuple[Hub, ...]:
        rows = self._session().execute(select(HubRow)).scalars().all()
        return tuple(_hub_entity(row) for row in rows)


class _DropOffRepo(_Repo):
    def save(self, drop_off: DropOff) -> None:
        _stage(
            self._stage_for("drop_offs"),
            drop_off.drop_off_id,
            drop_off,
            drop_off.version,
        )

    def get(self, drop_off_id: UUID) -> DropOff | None:
        staged = self._stage_for("drop_offs").get(drop_off_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DropOffRow, drop_off_id)
        return _drop_off_entity(row) if row is not None else None

    def find_by_tracking_code(self, tracking_code: str) -> DropOff | None:
        for staged, _ in self._stage_for("drop_offs").values():
            if staged.tracking_code == tracking_code:
                return staged
        row = (
            self._session()
            .execute(
                select(DropOffRow)
                .where(
                    DropOffRow.tracking_code == tracking_code,
                    DropOffRow.status.in_(_OPEN_DROP_OFF_VALUES),
                )
            )
            .scalars()
            .first()
        )
        return _drop_off_entity(row) if row is not None else None

    def find_by_label_code(self, label_code: str) -> DropOff | None:
        row = (
            self._session()
            .execute(select(DropOffRow).where(DropOffRow.label_code == label_code))
            .scalars()
            .first()
        )
        return _drop_off_entity(row) if row is not None else None

    def list_open_for_hub(self, hub_id: UUID) -> tuple[DropOff, ...]:
        rows = (
            self._session()
            .execute(
                select(DropOffRow).where(
                    DropOffRow.hub_id == hub_id,
                    DropOffRow.status.in_(_OPEN_DROP_OFF_VALUES),
                )
            )
            .scalars()
            .all()
        )
        return tuple(_drop_off_entity(row) for row in rows)


class _PresenceRepo(_Repo):
    def save(self, presence: ParcelPresence) -> None:
        _stage(
            self._stage_for("presences"),
            presence.presence_id,
            presence,
            presence.version,
        )

    def get(self, presence_id: UUID) -> ParcelPresence | None:
        staged = self._stage_for("presences").get(presence_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(ParcelPresenceRow, presence_id)
        return _presence_entity(row) if row is not None else None

    def find_in_hub(self, hub_id: UUID, tracking_code: str) -> ParcelPresence | None:
        for staged, _ in self._stage_for("presences").values():
            if staged.hub_id == hub_id and staged.tracking_code == tracking_code:
                return staged
        row = (
            self._session()
            .execute(
                select(ParcelPresenceRow).where(
                    ParcelPresenceRow.hub_id == hub_id,
                    ParcelPresenceRow.tracking_code == tracking_code,
                )
            )
            .scalars()
            .first()
        )
        return _presence_entity(row) if row is not None else None

    def list_for_hub(self, hub_id: UUID) -> tuple[ParcelPresence, ...]:
        rows = (
            self._session()
            .execute(select(ParcelPresenceRow).where(ParcelPresenceRow.hub_id == hub_id))
            .scalars()
            .all()
        )
        return tuple(_presence_entity(row) for row in rows)

    def list_for_consignment(self, consignment_id: UUID) -> tuple[ParcelPresence, ...]:
        rows = (
            self._session()
            .execute(
                select(ParcelPresenceRow).where(
                    ParcelPresenceRow.consignment_id == consignment_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_presence_entity(row) for row in rows)


class _ConsignmentRepo(_Repo):
    def save(self, consignment: Consignment) -> None:
        _stage(
            self._stage_for("consignments"),
            consignment.consignment_id,
            consignment,
            consignment.version,
        )

    def get(self, consignment_id: UUID) -> Consignment | None:
        staged = self._stage_for("consignments").get(consignment_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(ConsignmentRow, consignment_id)
        return _consignment_entity(row) if row is not None else None

    def find_by_seal_code(self, seal_code: str) -> Consignment | None:
        row = (
            self._session()
            .execute(select(ConsignmentRow).where(ConsignmentRow.seal_code == seal_code))
            .scalars()
            .first()
        )
        return _consignment_entity(row) if row is not None else None

    def list_for_hub(self, hub_id: UUID) -> tuple[Consignment, ...]:
        rows = (
            self._session()
            .execute(
                select(ConsignmentRow).where(
                    (ConsignmentRow.origin_hub_id == hub_id)
                    | (ConsignmentRow.destination_hub_id == hub_id)
                )
            )
            .scalars()
            .all()
        )
        return tuple(_consignment_entity(row) for row in rows)


class _SealCheckRepo(_Repo):
    def save(self, check: SealCheck) -> None:
        self._stage_for("seal_checks")[check.check_id] = check

    def list_for_consignment(self, consignment_id: UUID) -> tuple[SealCheck, ...]:
        rows = (
            self._session()
            .execute(
                select(SealCheckRow).where(SealCheckRow.consignment_id == consignment_id)
            )
            .scalars()
            .all()
        )
        return tuple(_seal_check_entity(row) for row in rows)


class _LinehaulRepo(_Repo):
    def save(self, linehaul: Linehaul) -> None:
        _stage(
            self._stage_for("linehauls"),
            linehaul.linehaul_id,
            linehaul,
            linehaul.version,
        )

    def get(self, linehaul_id: UUID) -> Linehaul | None:
        staged = self._stage_for("linehauls").get(linehaul_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(LinehaulRow, linehaul_id)
        return _linehaul_entity(row) if row is not None else None

    def find_for_consignment(self, consignment_id: UUID) -> Linehaul | None:
        for staged, _ in self._stage_for("linehauls").values():
            if staged.consignment_id == consignment_id:
                return staged
        row = (
            self._session()
            .execute(
                select(LinehaulRow).where(LinehaulRow.consignment_id == consignment_id)
            )
            .scalars()
            .first()
        )
        return _linehaul_entity(row) if row is not None else None

    def list_for_hub(self, hub_id: UUID) -> tuple[Linehaul, ...]:
        rows = (
            self._session()
            .execute(
                select(LinehaulRow).where(
                    (LinehaulRow.origin_hub_id == hub_id)
                    | (LinehaulRow.destination_hub_id == hub_id)
                )
            )
            .scalars()
            .all()
        )
        return tuple(_linehaul_entity(row) for row in rows)


class _PositionRepo(_Repo):
    def save(self, position: VehiclePosition) -> None:
        self._stage_for("positions")[position.position_id] = position

    def list_for_linehaul(self, linehaul_id: UUID) -> tuple[VehiclePosition, ...]:
        rows = (
            self._session()
            .execute(
                select(VehiclePositionRow)
                .where(VehiclePositionRow.linehaul_id == linehaul_id)
                .order_by(VehiclePositionRow.recorded_at)
            )
            .scalars()
            .all()
        )
        return tuple(_position_entity(row) for row in rows)


class _OutboxRepo(_Repo):
    def insert(self, record: OutboxRecord) -> None:
        self._stage_for("outbox")[record.event_id] = record

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        staged = self._stage_for("outbox").get(event_id)
        if staged is not None:
            return staged
        row = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.event_id == event_id
                )
            )
            .scalars()
            .first()
        )
        return _outbox_entity(row) if row is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.status == OutboxStatus.PENDING.value
                )
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.aggregate_id == aggregate_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        staged = self._stage_for("inbox").get((consumer_name, event_id))
        if staged is not None:
            return staged[0]
        row = (
            self._session()
            .execute(
                select(IntegrationInboxRow).where(
                    IntegrationInboxRow.consumer_name == consumer_name,
                    IntegrationInboxRow.event_id == event_id,
                )
            )
            .scalars()
            .first()
        )
        return _inbox_entity(row) if row is not None else None

    def insert(self, record: InboxRecord) -> None:
        self._stage_for("inbox")[(record.consumer_name, record.event_id)] = (record, True)

    def save(self, record: InboxRecord) -> None:
        stage = self._stage_for("inbox")
        key = (record.consumer_name, record.event_id)
        is_new = stage[key][1] if key in stage else False
        stage[key] = (record, is_new)


# ------------------------------------------------------------------ mappers


def _hub_values(e: Hub) -> dict[str, object]:
    return {
        "code": e.code,
        "name": e.name,
        "governorate": e.governorate,
        "cut_off_local_time": e.cut_off.local_time,
        "is_active": e.is_active,
        "vehicle_cameras_fitted": e.vehicle_cameras_fitted,
        "version": e.version,
    }


def _hub_entity(row: HubRow) -> Hub:
    return Hub(
        hub_id=row.hub_id,  # type: ignore[arg-type]
        code=row.code,
        name=row.name,
        governorate=row.governorate,
        cut_off=CutOffTime(local_time=row.cut_off_local_time),  # type: ignore[arg-type]
        is_active=bool(row.is_active),
        vehicle_cameras_fitted=bool(row.vehicle_cameras_fitted),
        version=row.version,
    )


def _drop_off_values(e: DropOff) -> dict[str, object]:
    return {
        "hub_id": e.hub_id,
        "tracking_code": e.tracking_code,
        "status": e.status.value,
        "shipment_request_id": e.shipment_request_id,
        "sender_principal_id": e.sender_principal_id,
        "captured_details": dict(e.captured_details),
        "weight_grams": e.weight_grams,
        "label_code": e.label_code,
        "labelled_by_actor_id": e.labelled_by_actor_id,
        "created_at": e.created_at,
        "expires_at": e.expires_at,
        "accepted_at": e.accepted_at,
        "accepted_by_actor_id": e.accepted_by_actor_id,
        "closed_at": e.closed_at,
        "version": e.version,
    }


def _drop_off_entity(row: DropOffRow) -> DropOff:
    return DropOff(
        drop_off_id=row.drop_off_id,  # type: ignore[arg-type]
        hub_id=row.hub_id,  # type: ignore[arg-type]
        tracking_code=row.tracking_code,
        status=DropOffStatus(row.status),
        shipment_request_id=row.shipment_request_id,  # type: ignore[arg-type]
        sender_principal_id=row.sender_principal_id,  # type: ignore[arg-type]
        captured_details=dict(row.captured_details or {}),
        weight_grams=row.weight_grams,
        label_code=row.label_code,
        labelled_by_actor_id=row.labelled_by_actor_id,  # type: ignore[arg-type]
        created_at=row.created_at,  # type: ignore[arg-type]
        expires_at=row.expires_at,  # type: ignore[arg-type]
        accepted_at=row.accepted_at,  # type: ignore[arg-type]
        accepted_by_actor_id=row.accepted_by_actor_id,  # type: ignore[arg-type]
        closed_at=row.closed_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _presence_values(e: ParcelPresence) -> dict[str, object]:
    return {
        "hub_id": e.hub_id,
        "tracking_code": e.tracking_code,
        "status": e.status.value,
        "destination_governorate": e.destination_governorate,
        "urgency": e.urgency.value,
        "routing_decision": (
            e.routing_decision.value if e.routing_decision is not None else None
        ),
        "route_code": e.route_code,
        "consignment_id": e.consignment_id,
        "received_at": e.received_at,
        "sorted_at": e.sorted_at,
        "departed_at": e.departed_at,
        "handed_to_last_mile_at": e.handed_to_last_mile_at,
        "hold_reason": e.hold_reason.value if e.hold_reason is not None else None,
        "disposition": e.disposition.value if e.disposition is not None else None,
        "version": e.version,
    }


def _presence_entity(row: ParcelPresenceRow) -> ParcelPresence:
    return ParcelPresence(
        presence_id=row.presence_id,  # type: ignore[arg-type]
        hub_id=row.hub_id,  # type: ignore[arg-type]
        tracking_code=row.tracking_code,
        status=ParcelPresenceStatus(row.status),
        destination_governorate=row.destination_governorate,
        urgency=Urgency(row.urgency),
        routing_decision=(
            RoutingDecision(row.routing_decision) if row.routing_decision else None
        ),
        route_code=row.route_code,
        consignment_id=row.consignment_id,  # type: ignore[arg-type]
        received_at=row.received_at,  # type: ignore[arg-type]
        sorted_at=row.sorted_at,  # type: ignore[arg-type]
        departed_at=row.departed_at,  # type: ignore[arg-type]
        handed_to_last_mile_at=row.handed_to_last_mile_at,  # type: ignore[arg-type]
        hold_reason=HoldReason(row.hold_reason) if row.hold_reason else None,
        disposition=ParcelDisposition(row.disposition) if row.disposition else None,
        version=row.version,
    )


def _consignment_values(e: Consignment) -> dict[str, object]:
    return {
        "origin_hub_id": e.origin_hub_id,
        "destination_hub_id": e.destination_hub_id,
        "status": e.status.value,
        "seal_code": e.seal.seal_code if e.seal else None,
        "seal_applied_at": e.seal.applied_at if e.seal else None,
        "seal_applied_by_actor_id": e.seal.applied_by_actor_id if e.seal else None,
        "parcel_codes": list(e.parcel_codes),
        "created_at": e.created_at,
        "dispatched_at": e.dispatched_at,
        "arrived_at": e.arrived_at,
        "reconciled_at": e.reconciled_at,
        "version": e.version,
    }


def _consignment_entity(row: ConsignmentRow) -> Consignment:
    seal = None
    if row.seal_code is not None and row.seal_applied_at is not None:
        seal = SecuritySeal(
            seal_code=row.seal_code,
            applied_at=row.seal_applied_at,  # type: ignore[arg-type]
            applied_by_actor_id=row.seal_applied_by_actor_id,  # type: ignore[arg-type]
        )
    return Consignment(
        consignment_id=row.consignment_id,  # type: ignore[arg-type]
        origin_hub_id=row.origin_hub_id,  # type: ignore[arg-type]
        destination_hub_id=row.destination_hub_id,  # type: ignore[arg-type]
        status=ConsignmentStatus(row.status),
        seal=seal,
        parcel_codes=tuple(row.parcel_codes or ()),
        created_at=row.created_at,  # type: ignore[arg-type]
        dispatched_at=row.dispatched_at,  # type: ignore[arg-type]
        arrived_at=row.arrived_at,  # type: ignore[arg-type]
        reconciled_at=row.reconciled_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _seal_check_values(e: SealCheck) -> dict[str, object]:
    return {
        "check_id": e.check_id,
        "consignment_id": e.consignment_id,
        "hub_id": e.hub_id,
        "expected_seal_code": e.expected_seal_code,
        "observed_seal_code": e.observed_seal_code,
        "outcome": e.outcome.value,
        "checked_at": e.checked_at,
        "checked_by_actor_id": e.checked_by_actor_id,
    }


def _seal_check_entity(row: SealCheckRow) -> SealCheck:
    return SealCheck(
        check_id=row.check_id,  # type: ignore[arg-type]
        consignment_id=row.consignment_id,  # type: ignore[arg-type]
        hub_id=row.hub_id,  # type: ignore[arg-type]
        expected_seal_code=row.expected_seal_code,
        observed_seal_code=row.observed_seal_code,
        outcome=SealCheckOutcome(row.outcome),
        checked_at=row.checked_at,  # type: ignore[arg-type]
        checked_by_actor_id=row.checked_by_actor_id,  # type: ignore[arg-type]
    )


def _linehaul_values(e: Linehaul) -> dict[str, object]:
    return {
        "consignment_id": e.consignment_id,
        "origin_hub_id": e.origin_hub_id,
        "destination_hub_id": e.destination_hub_id,
        "vehicle_reference": e.vehicle_reference,
        "driver_principal_id": e.driver_principal_id,
        "status": e.status.value,
        "planned_departure_at": e.planned_departure_at,
        "departed_at": e.departed_at,
        "expected_arrival_at": e.expected_arrival_at,
        "arrived_at": e.arrived_at,
        "route_deviation_flagged": e.route_deviation_flagged,
        "deviation_note": e.deviation_note,
        "version": e.version,
    }


def _linehaul_entity(row: LinehaulRow) -> Linehaul:
    return Linehaul(
        linehaul_id=row.linehaul_id,  # type: ignore[arg-type]
        consignment_id=row.consignment_id,  # type: ignore[arg-type]
        origin_hub_id=row.origin_hub_id,  # type: ignore[arg-type]
        destination_hub_id=row.destination_hub_id,  # type: ignore[arg-type]
        vehicle_reference=row.vehicle_reference,
        driver_principal_id=row.driver_principal_id,  # type: ignore[arg-type]
        status=LinehaulStatus(row.status),
        planned_departure_at=row.planned_departure_at,  # type: ignore[arg-type]
        departed_at=row.departed_at,  # type: ignore[arg-type]
        expected_arrival_at=row.expected_arrival_at,  # type: ignore[arg-type]
        arrived_at=row.arrived_at,  # type: ignore[arg-type]
        route_deviation_flagged=bool(row.route_deviation_flagged),
        deviation_note=row.deviation_note,
        version=row.version,
    )


def _position_values(e: VehiclePosition) -> dict[str, object]:
    return {
        "position_id": e.position_id,
        "linehaul_id": e.linehaul_id,
        "source": e.source.value,
        "latitude": e.point.latitude,
        "longitude": e.point.longitude,
        "recorded_at": e.recorded_at,
    }


def _position_entity(row: VehiclePositionRow) -> VehiclePosition:
    return VehiclePosition(
        position_id=row.position_id,  # type: ignore[arg-type]
        linehaul_id=row.linehaul_id,  # type: ignore[arg-type]
        source=PositionSource(row.source),
        point=GeoPoint(
            latitude=Decimal(str(row.latitude)), longitude=Decimal(str(row.longitude))
        ),
        recorded_at=row.recorded_at,  # type: ignore[arg-type]
    )


def _outbox_values(e: OutboxRecord) -> dict[str, object]:
    return {
        "id": e.id,
        "event_id": e.event_id,
        "subject": e.subject,
        "event_type": e.event_type,
        "event_version": e.event_version,
        "aggregate_id": e.aggregate_id,
        "aggregate_version": e.aggregate_version,
        "payload_json": e.payload_json,
        "status": e.status.value,
        "attempt_count": e.attempt_count,
        "max_attempts": e.max_attempts,
        "next_attempt_at": e.next_attempt_at,
        "processing_owner": e.processing_owner,
        "processing_until": e.processing_until,
        "published_at": e.published_at,
        "last_error_code": e.last_error_code,
        "last_error_message": e.last_error_message,
        "created_at": e.created_at,
    }


def _outbox_entity(row: IntegrationOutboxRow) -> OutboxRecord:
    return OutboxRecord(
        id=row.id,  # type: ignore[arg-type]
        event_id=row.event_id,  # type: ignore[arg-type]
        subject=row.subject,
        event_type=row.event_type,
        event_version=row.event_version,
        aggregate_id=row.aggregate_id,  # type: ignore[arg-type]
        aggregate_version=row.aggregate_version,
        payload_json=dict(row.payload_json or {}),
        status=OutboxStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,  # type: ignore[arg-type]
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,  # type: ignore[arg-type]
        published_at=row.published_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,  # type: ignore[arg-type]
    )


def _inbox_values(e: InboxRecord) -> dict[str, object]:
    return {
        "inbox_id": e.inbox_id,
        "consumer_name": e.consumer_name,
        "event_id": e.event_id,
        **_inbox_update_values(e),
    }


def _inbox_update_values(e: InboxRecord) -> dict[str, object]:
    return {
        "event_type": e.event_type,
        "event_version": e.event_version,
        "status": e.status.value,
        "received_at": e.received_at,
        "attempt_count": e.attempt_count,
        "processing_started_at": e.processing_started_at,
        "processing_lease_until": e.processing_lease_until,
        "processed_at": e.processed_at,
        "last_error_code": e.last_error_code,
        "payload_json": e.payload_json,
    }


def _inbox_entity(row: IntegrationInboxRow) -> InboxRecord:
    return InboxRecord(
        inbox_id=row.inbox_id,  # type: ignore[arg-type]
        consumer_name=row.consumer_name,
        event_id=row.event_id,  # type: ignore[arg-type]
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        received_at=row.received_at,  # type: ignore[arg-type]
        attempt_count=row.attempt_count,
        processing_started_at=row.processing_started_at,  # type: ignore[arg-type]
        processing_lease_until=row.processing_lease_until,  # type: ignore[arg-type]
        processed_at=row.processed_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        payload_json=dict(row.payload_json or {}),
    )
