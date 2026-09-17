"""PostgreSQL unit of work for the Ordering service.

Writes are staged during the transaction and flushed on commit with a version-conditional
UPDATE, so a lost update surfaces as `StaleOrderingRecord` rather than silently winning.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from ordering.domain.entities import (
    GoodsCategory,
    Order,
    SenderRef,
    ShipmentRequest,
    TariffRate,
)
from ordering.domain.errors import StaleOrderingRecord
from ordering.domain.messaging import (
    InboxRecord,
    InboxStatus,
    OutboxRecord,
    OutboxStatus,
)
from ordering.domain.money import Currency, Money
from ordering.domain.value_objects import (
    CancellationReason,
    DeliveryFeePayer,
    GeoPoint,
    OrderStatus,
    ParcelMeasurements,
    PaymentTerms,
    ReceiverDetails,
    RequestStatus,
    SenderKind,
    ShipmentAddOns,
)
from ordering.infrastructure.persistence.models import (
    GoodsCategoryRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    OrderRow,
    ShipmentRequestRow,
    TariffRateRow,
)

_STAGES = ("orders", "requests", "goods", "tariffs", "outbox", "inbox")


class SqlAlchemyOrderingUnitOfWork:
    """One instance serves every request, so its transaction is request-scoped.

    ``create_app`` builds this once and puts it on ``app.state``. Holding the
    open session on ``self`` meant two requests in flight shared it, and the
    second ``begin()`` raised ``transaction already active`` — a 500 for any two
    simultaneous users. ``_session`` and ``_pending`` are therefore properties
    over :class:`~contextvars.ContextVar`, which FastAPI gives a fresh copy of
    per request, so every existing call site keeps working unchanged.
    """

    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session_var: ContextVar[SaSession | None] = ContextVar(
            "ordering_session", default=None
        )
        self._pending_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "ordering_pending", default=None
        )
        self._orders = _OrderRepo(self)
        self._requests = _RequestRepo(self)
        self._goods = _GoodsRepo(self)
        self._tariffs = _TariffRepo(self)
        self._outbox = _OutboxRepo(self)
        self._inbox = _InboxRepo(self)

    # --------------------------------------------------- request-scoped state

    @property
    def _session(self) -> SaSession | None:
        return self._session_var.get()

    @_session.setter
    def _session(self, value: SaSession | None) -> None:
        self._session_var.set(value)

    @property
    def _pending(self) -> dict[str, dict] | None:
        return self._pending_var.get()

    @_pending.setter
    def _pending(self, value: dict[str, dict] | None) -> None:
        self._pending_var.set(value)

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
        for entity, previous in pending["orders"].values():
            _versioned(
                session,
                OrderRow,
                OrderRow.order_id == entity.order_id,
                previous=previous,
                values=_order_values(entity),
                new_row=lambda e=entity: OrderRow(order_id=e.order_id, **_order_values(e)),
            )
        for entity, previous in pending["requests"].values():
            _versioned(
                session,
                ShipmentRequestRow,
                ShipmentRequestRow.request_id == entity.request_id,
                previous=previous,
                values=_request_values(entity),
                new_row=lambda e=entity: ShipmentRequestRow(
                    request_id=e.request_id, **_request_values(e)
                ),
            )
        for entity in pending["goods"].values():
            session.merge(GoodsCategoryRow(code=entity.code, **_goods_values(entity)))
        for entity, previous in pending["tariffs"].values():
            _versioned(
                session,
                TariffRateRow,
                TariffRateRow.tariff_id == entity.tariff_id,
                previous=previous,
                values=_tariff_values(entity),
                new_row=lambda e=entity: TariffRateRow(
                    tariff_id=e.tariff_id, **_tariff_values(e)
                ),
            )
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


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleOrderingRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _Repo:
    def __init__(self, uow: SqlAlchemyOrderingUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _OrderRepo(_Repo):
    def save(self, order: Order) -> None:
        _stage(self._stage_for("orders"), order.order_id, order, order.version)

    def get(self, order_id: UUID) -> Order | None:
        staged = self._stage_for("orders").get(order_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(OrderRow, order_id)
        return _order_entity(row) if row is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[Order, ...]:
        rows = (
            self._session()
            .execute(select(OrderRow).where(OrderRow.sender_principal_id == principal_id))
            .scalars()
            .all()
        )
        return tuple(_order_entity(row) for row in rows)


class _RequestRepo(_Repo):
    def save(self, request: ShipmentRequest) -> None:
        _stage(
            self._stage_for("requests"), request.request_id, request, request.version
        )

    def get(self, request_id: UUID) -> ShipmentRequest | None:
        staged = self._stage_for("requests").get(request_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(ShipmentRequestRow, request_id)
        return _request_entity(row) if row is not None else None

    def find_by_tracking_code(self, tracking_code: str) -> ShipmentRequest | None:
        for staged, _ in self._stage_for("requests").values():
            if staged.tracking_code == tracking_code:
                return staged
        row = (
            self._session()
            .execute(
                select(ShipmentRequestRow).where(
                    ShipmentRequestRow.tracking_code == tracking_code
                )
            )
            .scalars()
            .first()
        )
        return _request_entity(row) if row is not None else None

    def find_by_label_code(self, label_code: str) -> ShipmentRequest | None:
        for staged, _ in self._stage_for("requests").values():
            if staged.label_code == label_code:
                return staged
        row = (
            self._session()
            .execute(
                select(ShipmentRequestRow).where(
                    ShipmentRequestRow.label_code == label_code,
                    ShipmentRequestRow.status != RequestStatus.CANCELLED.value,
                )
            )
            .scalars()
            .first()
        )
        return _request_entity(row) if row is not None else None

    def list_for_order(self, order_id: UUID) -> tuple[ShipmentRequest, ...]:
        rows = (
            self._session()
            .execute(
                select(ShipmentRequestRow).where(ShipmentRequestRow.order_id == order_id)
            )
            .scalars()
            .all()
        )
        return tuple(_request_entity(row) for row in rows)

    def list_for_principal(self, principal_id: UUID) -> tuple[ShipmentRequest, ...]:
        rows = (
            self._session()
            .execute(
                select(ShipmentRequestRow).where(
                    ShipmentRequestRow.sender_principal_id == principal_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_request_entity(row) for row in rows)

    def list_for_merchant(self, merchant_id: UUID) -> tuple[ShipmentRequest, ...]:
        rows = (
            self._session()
            .execute(
                select(ShipmentRequestRow).where(
                    ShipmentRequestRow.sender_merchant_id == merchant_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_request_entity(row) for row in rows)


class _GoodsRepo(_Repo):
    def save(self, category: GoodsCategory) -> None:
        self._stage_for("goods")[category.code] = category

    def get(self, code: str) -> GoodsCategory | None:
        staged = self._stage_for("goods").get(code)
        if staged is not None:
            return staged
        row = self._session().get(GoodsCategoryRow, code)
        return _goods_entity(row) if row is not None else None

    def list_active(self) -> tuple[GoodsCategory, ...]:
        rows = (
            self._session()
            .execute(select(GoodsCategoryRow).where(GoodsCategoryRow.archived_at.is_(None)))
            .scalars()
            .all()
        )
        return tuple(_goods_entity(row) for row in rows)


class _TariffRepo(_Repo):
    def save(self, rate: TariffRate) -> None:
        _stage(self._stage_for("tariffs"), rate.tariff_id, rate, rate.version)

    def find_rate(
        self, *, origin: str, destination: str, moment: datetime
    ) -> TariffRate | None:
        rows = (
            self._session()
            .execute(
                select(TariffRateRow)
                .where(
                    TariffRateRow.origin_governorate == origin,
                    TariffRateRow.destination_governorate == destination,
                    TariffRateRow.effective_from <= moment,
                )
                .order_by(TariffRateRow.effective_from.desc())
            )
            .scalars()
            .all()
        )
        for row in rows:
            rate = _tariff_entity(row)
            if rate.covers(moment):
                return rate
        return None

    def list_rates(self) -> tuple[TariffRate, ...]:
        rows = self._session().execute(select(TariffRateRow)).scalars().all()
        return tuple(_tariff_entity(row) for row in rows)


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


def _sender_values(sender: SenderRef, prefix: str = "sender_") -> dict[str, object]:
    return {
        f"{prefix}kind": sender.kind.value,
        f"{prefix}principal_id": sender.principal_id,
        f"{prefix}merchant_id": sender.merchant_id,
        f"{prefix}store_id": sender.store_id,
    }


def _sender_entity(row, prefix: str = "sender_") -> SenderRef:
    return SenderRef(
        kind=SenderKind(getattr(row, f"{prefix}kind")),
        principal_id=getattr(row, f"{prefix}principal_id"),
        merchant_id=getattr(row, f"{prefix}merchant_id"),
        store_id=getattr(row, f"{prefix}store_id"),
    )


def _order_values(e: Order) -> dict[str, object]:
    return {
        "reference": e.reference,
        **_sender_values(e.sender),
        "status": e.status.value,
        "created_at": e.created_at,
        "submitted_at": e.submitted_at,
        "cancelled_at": e.cancelled_at,
        "version": e.version,
    }


def _order_entity(row: OrderRow) -> Order:
    return Order(
        order_id=row.order_id,  # type: ignore[arg-type]
        sender=_sender_entity(row),
        status=OrderStatus(row.status),
        reference=row.reference,
        created_at=row.created_at,  # type: ignore[arg-type]
        submitted_at=row.submitted_at,  # type: ignore[arg-type]
        cancelled_at=row.cancelled_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _request_values(e: ShipmentRequest) -> dict[str, object]:
    return {
        "order_id": e.order_id,
        "tracking_code": e.tracking_code,
        **_sender_values(e.sender),
        "receiver_phone": e.receiver.phone,
        "receiver_governorate": e.receiver.governorate,
        "receiver_name": e.receiver.name,
        "receiver_address_line": e.receiver.address_line,
        "receiver_landmark": e.receiver.landmark,
        "receiver_latitude": e.receiver.geo.latitude if e.receiver.geo else None,
        "receiver_longitude": e.receiver.geo.longitude if e.receiver.geo else None,
        "description": e.description,
        "goods_category_code": e.goods_category_code,
        "status": e.status.value,
        "weight_grams": e.measurements.weight_grams,
        "length_cm": e.measurements.length_cm,
        "width_cm": e.measurements.width_cm,
        "height_cm": e.measurements.height_cm,
        "payment_terms": e.payment_terms.value,
        "cod_amount_minor_units": e.cod_amount.minor_units if e.cod_amount else None,
        "cod_currency": e.cod_amount.currency.value if e.cod_amount else None,
        "open_box_allowed": e.add_ons.open_box_allowed,
        "photo_documentation": e.add_ons.photo_documentation,
        "hudhud_packaging": e.add_ons.hudhud_packaging,
        "delivery_fee_payer": e.add_ons.delivery_fee_payer.value,
        "label_code": e.label_code,
        "label_linked_at": e.label_linked_at,
        "pickup_store_id": e.pickup_store_id,
        "pickup_window_start": e.pickup_window_start,
        "pickup_window_end": e.pickup_window_end,
        "assigned_courier_id": e.assigned_courier_id,
        "delivery_fee_minor_units": (
            e.delivery_fee.minor_units if e.delivery_fee else None
        ),
        "packaging_fee_minor_units": (
            e.packaging_fee.minor_units if e.packaging_fee else None
        ),
        "fee_currency": e.delivery_fee.currency.value if e.delivery_fee else None,
        "tariff_reference": e.tariff_reference,
        "created_at": e.created_at,
        "registered_at": e.registered_at,
        "cancelled_at": e.cancelled_at,
        "cancellation_reason": (
            e.cancellation_reason.value if e.cancellation_reason else None
        ),
        "custody_started_at": e.custody_started_at,
        "courier_at_receiver_at": e.courier_at_receiver_at,
        "version": e.version,
    }


def _request_entity(row: ShipmentRequestRow) -> ShipmentRequest:
    geo = None
    if row.receiver_latitude is not None and row.receiver_longitude is not None:
        geo = GeoPoint(
            latitude=Decimal(str(row.receiver_latitude)),
            longitude=Decimal(str(row.receiver_longitude)),
        )
    cod = None
    if row.cod_amount_minor_units is not None and row.cod_currency is not None:
        cod = Money(int(row.cod_amount_minor_units), Currency(row.cod_currency))
    delivery_fee = None
    packaging_fee = None
    if row.fee_currency is not None:
        currency = Currency(row.fee_currency)
        if row.delivery_fee_minor_units is not None:
            delivery_fee = Money(int(row.delivery_fee_minor_units), currency)
        if row.packaging_fee_minor_units is not None:
            packaging_fee = Money(int(row.packaging_fee_minor_units), currency)
    return ShipmentRequest(
        request_id=row.request_id,  # type: ignore[arg-type]
        order_id=row.order_id,  # type: ignore[arg-type]
        sender=_sender_entity(row),
        tracking_code=row.tracking_code,
        receiver=ReceiverDetails(
            phone=row.receiver_phone,
            governorate=row.receiver_governorate,
            name=row.receiver_name,
            address_line=row.receiver_address_line,
            landmark=row.receiver_landmark,
            geo=geo,
        ),
        description=row.description,
        status=RequestStatus(row.status),
        goods_category_code=row.goods_category_code,
        measurements=ParcelMeasurements(
            weight_grams=row.weight_grams,
            length_cm=row.length_cm,
            width_cm=row.width_cm,
            height_cm=row.height_cm,
        ),
        payment_terms=PaymentTerms(row.payment_terms),
        cod_amount=cod,
        add_ons=ShipmentAddOns(
            open_box_allowed=bool(row.open_box_allowed),
            photo_documentation=bool(row.photo_documentation),
            hudhud_packaging=bool(row.hudhud_packaging),
            delivery_fee_payer=DeliveryFeePayer(row.delivery_fee_payer),
        ),
        label_code=row.label_code,
        label_linked_at=row.label_linked_at,  # type: ignore[arg-type]
        pickup_store_id=row.pickup_store_id,  # type: ignore[arg-type]
        pickup_window_start=row.pickup_window_start,  # type: ignore[arg-type]
        pickup_window_end=row.pickup_window_end,  # type: ignore[arg-type]
        assigned_courier_id=row.assigned_courier_id,  # type: ignore[arg-type]
        delivery_fee=delivery_fee,
        packaging_fee=packaging_fee,
        tariff_reference=row.tariff_reference,
        created_at=row.created_at,  # type: ignore[arg-type]
        registered_at=row.registered_at,  # type: ignore[arg-type]
        cancelled_at=row.cancelled_at,  # type: ignore[arg-type]
        cancellation_reason=(
            CancellationReason(row.cancellation_reason)
            if row.cancellation_reason
            else None
        ),
        custody_started_at=row.custody_started_at,  # type: ignore[arg-type]
        courier_at_receiver_at=row.courier_at_receiver_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _goods_values(e: GoodsCategory) -> dict[str, object]:
    return {
        "display_name": e.display_name,
        "hint": e.hint,
        "restriction_note": e.restriction_note,
        "prohibited": e.prohibited,
        "sort_order": e.sort_order,
        "archived_at": e.archived_at,
    }


def _goods_entity(row: GoodsCategoryRow) -> GoodsCategory:
    return GoodsCategory(
        code=row.code,
        display_name=row.display_name,
        hint=row.hint,
        restriction_note=row.restriction_note,
        prohibited=bool(row.prohibited),
        sort_order=row.sort_order,
        archived_at=row.archived_at,  # type: ignore[arg-type]
    )


def _tariff_values(e: TariffRate) -> dict[str, object]:
    return {
        "reference": e.reference,
        "origin_governorate": e.origin_governorate,
        "destination_governorate": e.destination_governorate,
        "delivery_fee_minor_units": e.delivery_fee.minor_units,
        "packaging_fee_minor_units": e.packaging_fee.minor_units,
        "currency": e.delivery_fee.currency.value,
        "effective_from": e.effective_from,
        "effective_to": e.effective_to,
        "version": e.version,
    }


def _tariff_entity(row: TariffRateRow) -> TariffRate:
    currency = Currency(row.currency)
    return TariffRate(
        tariff_id=row.tariff_id,  # type: ignore[arg-type]
        reference=row.reference,
        origin_governorate=row.origin_governorate,
        destination_governorate=row.destination_governorate,
        delivery_fee=Money(int(row.delivery_fee_minor_units), currency),
        packaging_fee=Money(int(row.packaging_fee_minor_units), currency),
        effective_from=row.effective_from,  # type: ignore[arg-type]
        effective_to=row.effective_to,  # type: ignore[arg-type]
        version=row.version,
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
