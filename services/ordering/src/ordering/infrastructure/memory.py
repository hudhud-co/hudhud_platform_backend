"""In-memory Ordering unit of work for unit tests.

Snapshot-per-transaction: ``begin`` deep-copies, ``commit`` swaps, ``rollback`` discards,
so a rolled-back command is genuinely invisible.
"""

from __future__ import annotations

import copy
from contextvars import ContextVar
from datetime import datetime
from uuid import UUID

from ordering.domain.entities import (
    GoodsCategory,
    Order,
    ShipmentRequest,
    TariffRate,
)
from ordering.domain.messaging import InboxRecord, OutboxRecord

_COLLECTIONS = ("orders", "requests", "goods", "tariffs", "outbox", "inbox")


class InMemoryOrderingUnitOfWork:
    def __init__(self) -> None:
        self._committed: dict[str, dict] = {name: {} for name in _COLLECTIONS}
        self._tx_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "ordering_memory_tx", default=None
        )
        self._orders = _OrderRepo(self)
        self._requests = _RequestRepo(self)
        self._goods = _GoodsRepo(self)
        self._tariffs = _TariffRepo(self)
        self._outbox = _OutboxRepo(self)
        self._inbox = _InboxRepo(self)

    @property
    def _tx(self) -> dict[str, dict] | None:
        return self._tx_var.get()

    def begin(self) -> None:
        if self._tx_var.get() is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._tx_var.set(copy.deepcopy(self._committed))

    def commit(self) -> None:
        tx = self._tx_var.get()
        if tx is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._committed = tx
        self._tx_var.set(None)

    def rollback(self) -> None:
        self._tx_var.set(None)

    def _working(self, name: str) -> dict:
        return self._tx[name] if self._tx is not None else self._committed[name]

    @property
    def orders(self) -> _OrderRepo:
        return self._orders

    @property
    def requests(self) -> _RequestRepo:
        return self._requests

    @property
    def goods(self) -> _GoodsRepo:
        return self._goods

    @property
    def tariffs(self) -> _TariffRepo:
        return self._tariffs

    @property
    def outbox(self) -> _OutboxRepo:
        return self._outbox

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox


class _Repo:
    def __init__(self, store: InMemoryOrderingUnitOfWork) -> None:
        self._store = store


class _OrderRepo(_Repo):
    def save(self, order: Order) -> None:
        self._store._working("orders")[order.order_id] = copy.deepcopy(order)

    def get(self, order_id: UUID) -> Order | None:
        found = self._store._working("orders").get(order_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[Order, ...]:
        return tuple(
            copy.deepcopy(order)
            for order in self._store._working("orders").values()
            if order.sender.principal_id == principal_id
        )


class _RequestRepo(_Repo):
    def save(self, request: ShipmentRequest) -> None:
        self._store._working("requests")[request.request_id] = copy.deepcopy(request)

    def get(self, request_id: UUID) -> ShipmentRequest | None:
        found = self._store._working("requests").get(request_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_tracking_code(self, tracking_code: str) -> ShipmentRequest | None:
        for request in self._store._working("requests").values():
            if request.tracking_code == tracking_code:
                return copy.deepcopy(request)
        return None

    def find_by_label_code(self, label_code: str) -> ShipmentRequest | None:
        for request in self._store._working("requests").values():
            if request.label_code == label_code:
                return copy.deepcopy(request)
        return None

    def list_for_order(self, order_id: UUID) -> tuple[ShipmentRequest, ...]:
        return tuple(
            copy.deepcopy(request)
            for request in self._store._working("requests").values()
            if request.order_id == order_id
        )

    def list_for_principal(self, principal_id: UUID) -> tuple[ShipmentRequest, ...]:
        return tuple(
            copy.deepcopy(request)
            for request in self._store._working("requests").values()
            if request.sender.principal_id == principal_id
        )

    def list_for_merchant(self, merchant_id: UUID) -> tuple[ShipmentRequest, ...]:
        return tuple(
            copy.deepcopy(request)
            for request in self._store._working("requests").values()
            if request.sender.merchant_id == merchant_id
        )


class _GoodsRepo(_Repo):
    def save(self, category: GoodsCategory) -> None:
        self._store._working("goods")[category.code] = copy.deepcopy(category)

    def get(self, code: str) -> GoodsCategory | None:
        found = self._store._working("goods").get(code)
        return copy.deepcopy(found) if found is not None else None

    def list_active(self) -> tuple[GoodsCategory, ...]:
        return tuple(
            copy.deepcopy(category)
            for category in self._store._working("goods").values()
            if category.is_active
        )


class _TariffRepo(_Repo):
    def save(self, rate: TariffRate) -> None:
        self._store._working("tariffs")[rate.tariff_id] = copy.deepcopy(rate)

    def find_rate(
        self, *, origin: str, destination: str, moment: datetime
    ) -> TariffRate | None:
        candidates = [
            rate
            for rate in self._store._working("tariffs").values()
            if rate.origin_governorate == origin
            and rate.destination_governorate == destination
            and rate.covers(moment)
        ]
        if not candidates:
            return None
        # The most recently effective rate wins when windows overlap.
        return copy.deepcopy(max(candidates, key=lambda rate: rate.effective_from))

    def list_rates(self) -> tuple[TariffRate, ...]:
        return tuple(
            copy.deepcopy(rate) for rate in self._store._working("tariffs").values()
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
