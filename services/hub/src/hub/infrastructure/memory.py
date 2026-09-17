"""In-memory Hub unit of work for unit tests."""

from __future__ import annotations

import copy
from uuid import UUID

from hub.domain.entities import (
    Consignment,
    DropOff,
    Hub,
    Linehaul,
    ParcelPresence,
    SealCheck,
    VehiclePosition,
)
from hub.domain.messaging import InboxRecord, OutboxRecord
from hub.domain.value_objects import DROP_OFF_TRANSITIONS

_COLLECTIONS = (
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


class InMemoryHubDatabase:
    """The rows. Shared by every request, exactly as a database is.

    Split from the unit of work deliberately. A unit of work is *one request's*
    transaction; the rows outlive it. Holding both in one object is what allowed a single
    instance to be stored on `app.state` and serve every request at once — measured
    against real PostgreSQL, 1 of 40 concurrent requests succeeded and the rest raised
    "transaction already active".
    """

    def __init__(self) -> None:
        self.collections: dict[str, dict] = {name: {} for name in _COLLECTIONS}


class InMemoryHubUnitOfWork:
    def __init__(self, database: InMemoryHubDatabase | None = None) -> None:
        self._database = database or InMemoryHubDatabase()
        self._tx: dict[str, dict] | None = None
        self._hubs = _HubRepo(self)
        self._drop_offs = _DropOffRepo(self)
        self._presences = _PresenceRepo(self)
        self._consignments = _ConsignmentRepo(self)
        self._seal_checks = _SealCheckRepo(self)
        self._linehauls = _LinehaulRepo(self)
        self._positions = _PositionRepo(self)
        self._outbox = _OutboxRepo(self)
        self._inbox = _InboxRepo(self)

    @property
    def database(self) -> InMemoryHubDatabase:
        """The shared rows, so a factory can give the next request the same data."""
        return self._database

    def new_unit_of_work(self) -> InMemoryHubUnitOfWork:
        """Another transaction over the same rows — one per request."""
        return InMemoryHubUnitOfWork(self._database)


    def as_committed(self) -> InMemoryHubUnitOfWork:
        """A view whose "transaction" is the committed rows, for inspection and seeding.

        Service code never uses this: a service opens a real transaction, and
        :meth:`_working` refuses it otherwise. Tests use it to look at what was committed
        without opening one, and to seed rows before exercising a service — both of which
        would otherwise have to wrap every assertion in begin/rollback and would read far
        worse for no extra safety.

        Writes through this view land straight in the committed rows, which is what
        seeding wants and why it is named for it.
        """
        view = InMemoryHubUnitOfWork(self._database)
        view._tx = self._database.collections
        return view

    def begin(self) -> None:
        if self._tx is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._tx = copy.deepcopy(self._database.collections)

    def commit(self) -> None:
        if self._tx is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._database.collections = self._tx
        self._tx = None

    def rollback(self) -> None:
        self._tx = None

    def _working(self, name: str) -> dict:
        """Refuse a read or a write outside a transaction, as the store does.

        This used to fall back to the committed rows, making the double *more*
        permissive than SQLAlchemy — so a service method that forgot to open a
        transaction passed every unit test and failed only against PostgreSQL.
        """
        if self._tx is None:
            msg = (
                f"no active transaction: {name} was accessed outside begin()/commit(). "
                "The SQLAlchemy store raises here too."
            )
            raise RuntimeError(msg)
        return self._tx[name]

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


class _Repo:
    def __init__(self, store: InMemoryHubUnitOfWork) -> None:
        self._store = store


class _HubRepo(_Repo):
    def save(self, hub: Hub) -> None:
        self._store._working("hubs")[hub.hub_id] = copy.deepcopy(hub)

    def get(self, hub_id: UUID) -> Hub | None:
        found = self._store._working("hubs").get(hub_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_code(self, code: str) -> Hub | None:
        for facility in self._store._working("hubs").values():
            if facility.code == code:
                return copy.deepcopy(facility)
        return None

    def list_all(self) -> tuple[Hub, ...]:
        return tuple(
            copy.deepcopy(facility) for facility in self._store._working("hubs").values()
        )


class _DropOffRepo(_Repo):
    def save(self, drop_off: DropOff) -> None:
        self._store._working("drop_offs")[drop_off.drop_off_id] = copy.deepcopy(drop_off)

    def get(self, drop_off_id: UUID) -> DropOff | None:
        found = self._store._working("drop_offs").get(drop_off_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_tracking_code(self, tracking_code: str) -> DropOff | None:
        for drop_off in self._store._working("drop_offs").values():
            if drop_off.tracking_code == tracking_code:
                return copy.deepcopy(drop_off)
        return None

    def find_by_label_code(self, label_code: str) -> DropOff | None:
        for drop_off in self._store._working("drop_offs").values():
            if drop_off.label_code == label_code:
                return copy.deepcopy(drop_off)
        return None

    def list_open_for_hub(self, hub_id: UUID) -> tuple[DropOff, ...]:
        return tuple(
            copy.deepcopy(drop_off)
            for drop_off in self._store._working("drop_offs").values()
            if drop_off.hub_id == hub_id and DROP_OFF_TRANSITIONS[drop_off.status]
        )


class _PresenceRepo(_Repo):
    def save(self, presence: ParcelPresence) -> None:
        self._store._working("presences")[presence.presence_id] = copy.deepcopy(presence)

    def get(self, presence_id: UUID) -> ParcelPresence | None:
        found = self._store._working("presences").get(presence_id)
        return copy.deepcopy(found) if found is not None else None

    def find_in_hub(self, hub_id: UUID, tracking_code: str) -> ParcelPresence | None:
        for presence in self._store._working("presences").values():
            if presence.hub_id == hub_id and presence.tracking_code == tracking_code:
                return copy.deepcopy(presence)
        return None

    def list_for_hub(self, hub_id: UUID) -> tuple[ParcelPresence, ...]:
        return tuple(
            copy.deepcopy(presence)
            for presence in self._store._working("presences").values()
            if presence.hub_id == hub_id
        )

    def list_for_consignment(self, consignment_id: UUID) -> tuple[ParcelPresence, ...]:
        return tuple(
            copy.deepcopy(presence)
            for presence in self._store._working("presences").values()
            if presence.consignment_id == consignment_id
        )


class _ConsignmentRepo(_Repo):
    def save(self, consignment: Consignment) -> None:
        self._store._working("consignments")[consignment.consignment_id] = copy.deepcopy(
            consignment
        )

    def get(self, consignment_id: UUID) -> Consignment | None:
        found = self._store._working("consignments").get(consignment_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_seal_code(self, seal_code: str) -> Consignment | None:
        for consignment in self._store._working("consignments").values():
            if consignment.seal is not None and consignment.seal.seal_code == seal_code:
                return copy.deepcopy(consignment)
        return None

    def list_for_hub(self, hub_id: UUID) -> tuple[Consignment, ...]:
        return tuple(
            copy.deepcopy(consignment)
            for consignment in self._store._working("consignments").values()
            if hub_id in {consignment.origin_hub_id, consignment.destination_hub_id}
        )


class _SealCheckRepo(_Repo):
    def save(self, check: SealCheck) -> None:
        self._store._working("seal_checks")[check.check_id] = copy.deepcopy(check)

    def list_for_consignment(self, consignment_id: UUID) -> tuple[SealCheck, ...]:
        return tuple(
            copy.deepcopy(check)
            for check in self._store._working("seal_checks").values()
            if check.consignment_id == consignment_id
        )


class _LinehaulRepo(_Repo):
    def save(self, linehaul: Linehaul) -> None:
        self._store._working("linehauls")[linehaul.linehaul_id] = copy.deepcopy(linehaul)

    def get(self, linehaul_id: UUID) -> Linehaul | None:
        found = self._store._working("linehauls").get(linehaul_id)
        return copy.deepcopy(found) if found is not None else None

    def find_for_consignment(self, consignment_id: UUID) -> Linehaul | None:
        for linehaul in self._store._working("linehauls").values():
            if linehaul.consignment_id == consignment_id:
                return copy.deepcopy(linehaul)
        return None

    def list_for_hub(self, hub_id: UUID) -> tuple[Linehaul, ...]:
        return tuple(
            copy.deepcopy(linehaul)
            for linehaul in self._store._working("linehauls").values()
            if hub_id in {linehaul.origin_hub_id, linehaul.destination_hub_id}
        )


class _PositionRepo(_Repo):
    def save(self, position: VehiclePosition) -> None:
        self._store._working("positions")[position.position_id] = copy.deepcopy(position)

    def list_for_linehaul(self, linehaul_id: UUID) -> tuple[VehiclePosition, ...]:
        return tuple(
            copy.deepcopy(position)
            for position in self._store._working("positions").values()
            if position.linehaul_id == linehaul_id
        )


class _OutboxRepo(_Repo):
    def insert(self, record: OutboxRecord) -> None:
        rows = self._store._working("outbox")
        if record.event_id in rows:
            msg = f"duplicate outbox event_id: {record.event_id}"
            raise ValueError(msg)
        for existing in rows.values():
            if (
                existing.aggregate_id == record.aggregate_id
                and existing.aggregate_version == record.aggregate_version
            ):
                msg = (
                    "duplicate outbox aggregate version: "
                    f"{record.aggregate_id}@{record.aggregate_version}"
                )
                raise ValueError(msg)
        rows[record.event_id] = copy.deepcopy(record)

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        found = self._store._working("outbox").get(event_id)
        return copy.deepcopy(found) if found is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working("outbox").values()
            if record.status.value == "pending"
        )

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working("outbox").values()
            if record.aggregate_id == aggregate_id
        )


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        found = self._store._working("inbox").get((consumer_name, event_id))
        return copy.deepcopy(found) if found is not None else None

    def insert(self, record: InboxRecord) -> None:
        rows = self._store._working("inbox")
        key = (record.consumer_name, record.event_id)
        if key in rows:
            msg = f"duplicate inbox delivery: {key}"
            raise ValueError(msg)
        rows[key] = copy.deepcopy(record)

    def save(self, record: InboxRecord) -> None:
        self._store._working("inbox")[
            (record.consumer_name, record.event_id)
        ] = copy.deepcopy(record)
