"""Creating and registering shipments — the order-creation half of the journey.

The v6.3 rules this service exists to hold are all sender-kind rules, and they are
enforced here once rather than at each call site:

* a regular customer's parcel has **no COD** (p.8) and gets **no pickup** (p.8, p.15);
* a merchant registers by entering details, sticking a **pre-printed** label and scanning
  it (p.9, p.10) — a customer never labels their own parcel, hub staff do (p.18);
* weight and size are optional, a **description is required** (p.12);
* the receiver's phone and governorate are the only mandatory contact fields (p.12);
* pickup opens only once **every** parcel in the order carries a label (MER-19).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ordering.domain.entities import (
    Order,
    SenderRef,
    ShipmentRequest,
)
from ordering.domain.errors import (
    CashOnDeliveryNotAvailableForCustomers,
    CodAmountNotAllowed,
    CodAmountRequired,
    DescriptionRequired,
    InvalidLabelCode,
    InvalidPhoneNumber,
    LabelAlreadyLinked,
    LabelNotAllowedForCustomer,
    OrderNotFound,
    OrderNotOpen,
    PickupBlockedByMissingLabels,
    PickupNotAvailableForCustomers,
    ProhibitedGoods,
    ProhibitedGoodsNotAcknowledged,
    RequestTransitionNotAllowed,
    ShipmentRequestNotFound,
    UnknownGoodsCategory,
    UnknownGovernorate,
)
from ordering.domain.messaging import OutboxRecord, OutboxStatus
from ordering.domain.money import Money
from ordering.domain.value_objects import (
    OrderStatus,
    ParcelMeasurements,
    PaymentTerms,
    ReceiverDetails,
    RequestStatus,
    SenderKind,
    ShipmentAddOns,
    normalize_governorate,
    normalize_label_code,
    normalize_phone,
)
from ordering.infrastructure.contracts.envelopes import (
    build_shipment_registered_envelope,
)
from ordering.ports.repository import OrderingUnitOfWork

_CODE_DIGITS = 6


def _now() -> datetime:
    return datetime.now(tz=UTC)


def generate_tracking_code(moment: datetime) -> str:
    """`SHP-YYYYMMDD-NNNNNN`, exactly as the app displays it.

    The serial is random rather than sequential: a sequential code would let anyone
    enumerate every parcel in the network from a single tracking link.
    """
    serial = secrets.randbelow(10**_CODE_DIGITS)
    return f"SHP-{moment:%Y%m%d}-{serial:0{_CODE_DIGITS}d}"


@dataclass(frozen=True, slots=True)
class ReceiverDraft:
    phone: str
    governorate: str
    name: str | None = None
    address_line: str | None = None
    landmark: str | None = None


@dataclass(frozen=True, slots=True)
class ShipmentDraft:
    """Everything a sender types for one parcel."""

    receiver: ReceiverDraft
    description: str
    goods_category_code: str | None = None
    measurements: ParcelMeasurements = field(default_factory=ParcelMeasurements)
    payment_terms: PaymentTerms = PaymentTerms.PREPAID
    cod_amount: Money | None = None
    add_ons: ShipmentAddOns | None = None
    pickup_store_id: UUID | None = None
    prohibited_goods_acknowledged: bool = False


class SendService:
    def __init__(
        self,
        unit_of_work: OrderingUnitOfWork,
        *,
        outbox_max_attempts: int = 5,
        require_prohibited_acknowledgement: bool = True,
    ) -> None:
        self._uow = unit_of_work
        self._outbox_max_attempts = outbox_max_attempts
        self._require_acknowledgement = require_prohibited_acknowledgement

    # ------------------------------------------------------------- orders

    def open_order(self, *, sender: SenderRef) -> Order:
        """An order is the commercial agreement; its shipments are the parcels (p.14)."""
        self._uow.begin()
        try:
            moment = _now()
            order = Order(
                order_id=uuid4(),
                sender=sender,
                status=OrderStatus.OPEN,
                reference=f"ORD-{moment:%Y%m%d}-{secrets.randbelow(10**6):06d}",
                created_at=moment,
            )
            self._uow.orders.save(order)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return order

    # ------------------------------------------------------------- shipments

    def add_shipment(self, *, order_id: UUID, draft: ShipmentDraft) -> ShipmentRequest:
        """Add one parcel to an order, applying every sender-kind rule at once."""
        self._uow.begin()
        try:
            order = self._load_order(order_id)
            if not order.is_open:
                raise OrderNotOpen(order.status.value)
            request = self._build_request(order, draft)
            self._uow.requests.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    def add_shipments(
        self, *, order_id: UUID, drafts: tuple[ShipmentDraft, ...]
    ) -> tuple[ShipmentRequest, ...]:
        """Bulk creation (MER-18).

        All or nothing: a batch that half-succeeds would leave the merchant guessing which
        rows to retype, and would open a pickup on an incomplete order.
        """
        self._uow.begin()
        try:
            order = self._load_order(order_id)
            if not order.is_open:
                raise OrderNotOpen(order.status.value)
            created = []
            for draft in drafts:
                request = self._build_request(order, draft)
                self._uow.requests.save(request)
                created.append(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(created)

    def _build_request(self, order: Order, draft: ShipmentDraft) -> ShipmentRequest:
        sender = order.sender

        if self._require_acknowledgement and not draft.prohibited_goods_acknowledged:
            raise ProhibitedGoodsNotAcknowledged()

        description = draft.description.strip()
        if not description:
            raise DescriptionRequired()

        receiver = self._validate_receiver(draft.receiver)
        self._validate_goods(draft.goods_category_code)
        payment_terms, cod_amount = self._validate_payment(sender, draft)

        moment = _now()
        # A merchant's parcel waits for its pre-printed label; a customer's does not,
        # because hub staff label it at drop-off (v6.3 p.18).
        status = (
            RequestStatus.AWAITING_LABEL
            if sender.is_merchant
            else RequestStatus.AWAITING_DROPOFF
        )
        return ShipmentRequest(
            request_id=uuid4(),
            order_id=order.order_id,
            sender=sender,
            tracking_code=self._unique_tracking_code(moment),
            receiver=receiver,
            description=description,
            status=status,
            goods_category_code=draft.goods_category_code,
            measurements=draft.measurements,
            payment_terms=payment_terms,
            cod_amount=cod_amount,
            add_ons=draft.add_ons or ShipmentAddOns(),
            pickup_store_id=draft.pickup_store_id if sender.is_merchant else None,
            created_at=moment,
        )

    def _validate_receiver(self, draft: ReceiverDraft) -> ReceiverDetails:
        try:
            phone = normalize_phone(draft.phone)
        except ValueError as exc:
            raise InvalidPhoneNumber(draft.phone) from exc
        try:
            governorate = normalize_governorate(draft.governorate)
        except ValueError as exc:
            raise UnknownGovernorate(draft.governorate) from exc
        return ReceiverDetails(
            phone=phone,
            governorate=governorate,
            name=(draft.name or "").strip() or None,
            address_line=(draft.address_line or "").strip() or None,
            landmark=(draft.landmark or "").strip() or None,
        )

    def _validate_goods(self, code: str | None) -> None:
        if code is None:
            return
        category = self._uow.goods.get(code)
        if category is None or not category.is_active:
            raise UnknownGoodsCategory(code)
        if category.prohibited:
            raise ProhibitedGoods(code)

    def _validate_payment(
        self, sender: SenderRef, draft: ShipmentDraft
    ) -> tuple[PaymentTerms, Money | None]:
        terms = draft.payment_terms
        if terms is PaymentTerms.CASH_ON_DELIVERY:
            # v6.3 p.8, Confirmed decision. Refused for the sender kind, not for the
            # amount — so it cannot be worked around by sending zero.
            if sender.kind is SenderKind.CUSTOMER:
                raise CashOnDeliveryNotAvailableForCustomers()
            if draft.cod_amount is None or draft.cod_amount.is_zero:
                raise CodAmountRequired()
            return terms, draft.cod_amount
        if draft.cod_amount is not None and not draft.cod_amount.is_zero:
            raise CodAmountNotAllowed(terms.value)
        return terms, None

    def _unique_tracking_code(self, moment: datetime) -> str:
        for _ in range(12):
            candidate = generate_tracking_code(moment)
            if self._uow.requests.find_by_tracking_code(candidate) is None:
                return candidate
        msg = "could not allocate a unique tracking code"
        raise RuntimeError(msg)

    # ------------------------------------------------------------- labels

    def link_label(self, *, request_id: UUID, label_code: str) -> ShipmentRequest:
        """The merchant sticks a pre-printed label on and scans it (v6.3 p.9, p.10).

        Scanning is what links the physical box to the record. A customer never does this.
        """
        self._uow.begin()
        try:
            request = self._load_request(request_id)
            if not request.sender.is_merchant:
                raise LabelNotAllowedForCustomer()
            if request.status is not RequestStatus.AWAITING_LABEL:
                raise RequestTransitionNotAllowed(
                    request.status.value, RequestStatus.READY_FOR_PICKUP.value
                )
            try:
                code = normalize_label_code(label_code)
            except ValueError as exc:
                raise InvalidLabelCode(label_code) from exc

            existing = self._uow.requests.find_by_label_code(code)
            if existing is not None and existing.request_id != request_id:
                raise LabelAlreadyLinked(code)

            request.label_code = code
            request.label_linked_at = _now()
            request.status = RequestStatus.READY_FOR_PICKUP
            request.version += 1
            self._uow.requests.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    # ------------------------------------------------------------- pickup

    def order_pickup_readiness(self, *, order_id: UUID) -> tuple[int, int]:
        """`(labelled, total)` for the order's live shipments."""
        self._uow.begin()
        try:
            requests = self._uow.requests.list_for_order(order_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        live = [
            request
            for request in requests
            if request.status not in {RequestStatus.CANCELLED, RequestStatus.REGISTERED}
        ]
        return sum(1 for request in live if request.has_label), len(live)

    def book_pickup(
        self,
        *,
        order_id: UUID,
        window_start: datetime,
        window_end: datetime,
        store_id: UUID | None = None,
    ) -> tuple[ShipmentRequest, ...]:
        """Book a courier visit for a whole order.

        Two v6.3 rules meet here: a pickup is for merchants only (p.15), and the pickup
        time only opens once every parcel in the order carries a label (MER-19).
        """
        self._uow.begin()
        try:
            order = self._load_order(order_id)
            if order.sender.kind is SenderKind.CUSTOMER:
                raise PickupNotAvailableForCustomers()

            requests = [
                request
                for request in self._uow.requests.list_for_order(order_id)
                if request.status
                not in {RequestStatus.CANCELLED, RequestStatus.REGISTERED}
            ]
            unlabelled = [request for request in requests if not request.has_label]
            if unlabelled:
                raise PickupBlockedByMissingLabels(len(unlabelled))

            booked = []
            for request in requests:
                request.pickup_window_start = window_start
                request.pickup_window_end = window_end
                if store_id is not None:
                    request.pickup_store_id = store_id
                request.version += 1
                self._uow.requests.save(request)
                booked.append(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(booked)

    # ------------------------------------------------------------- registration

    def register(self, *, request_id: UUID) -> tuple[ShipmentRequest, UUID]:
        """Hand the parcel to the Shipment context.

        This is the last status Ordering owns. Everything after it — custody, checkpoint
        scans, the delivery outcome — belongs to Shipment (ADR-0003).
        """
        self._uow.begin()
        try:
            request = self._load_request(request_id)
            if not request.can_transition_to(RequestStatus.REGISTERED):
                raise RequestTransitionNotAllowed(
                    request.status.value, RequestStatus.REGISTERED.value
                )
            request.status = RequestStatus.REGISTERED
            request.registered_at = _now()
            request.version += 1
            self._uow.requests.save(request)

            event_id = uuid4()
            payload_json, subject = build_shipment_registered_envelope(
                request=request,
                aggregate_version=request.version,
                event_id=event_id,
                correlation_id=uuid4(),
            )
            moment = _now()
            self._uow.outbox.insert(
                OutboxRecord(
                    id=uuid4(),
                    event_id=event_id,
                    subject=subject,
                    event_type=payload_json["event_type"],
                    event_version=int(payload_json["event_version"]),
                    aggregate_id=request.request_id,
                    aggregate_version=request.version,
                    payload_json=payload_json,
                    status=OutboxStatus.PENDING,
                    attempt_count=0,
                    max_attempts=self._outbox_max_attempts,
                    next_attempt_at=moment,
                    created_at=moment,
                )
            )
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request, event_id

    # ------------------------------------------------------------- queries

    def get_request(self, request_id: UUID) -> ShipmentRequest:
        self._uow.begin()
        try:
            request = self._load_request(request_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    def get_order(self, order_id: UUID) -> Order:
        self._uow.begin()
        try:
            order = self._load_order(order_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return order

    def list_for_order(self, order_id: UUID) -> tuple[ShipmentRequest, ...]:
        self._uow.begin()
        try:
            found = self._uow.requests.list_for_order(order_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def list_for_principal(self, principal_id: UUID) -> tuple[ShipmentRequest, ...]:
        """Every request this principal authored, across all of their orders.

        The v3 `parcels` list and the `home` summary need this: without it a client can
        only read by `order_id`, so it would have to already know every order it had ever
        created in order to list its own parcels.
        """
        self._uow.begin()
        try:
            found = self._uow.requests.list_for_principal(principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def list_for_merchant(self, merchant_id: UUID) -> tuple[ShipmentRequest, ...]:
        """Every request sent on behalf of a merchant, whoever authored it.

        Authorization for the merchant is the caller's to establish; this method only
        reads. It backs the v3 store shipment list and the workplace branch views.
        """
        self._uow.begin()
        try:
            found = self._uow.requests.list_for_merchant(merchant_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _load_order(self, order_id: UUID) -> Order:
        order = self._uow.orders.get(order_id)
        if order is None:
            raise OrderNotFound(str(order_id))
        return order

    def _load_request(self, request_id: UUID) -> ShipmentRequest:
        request = self._uow.requests.get(request_id)
        if request is None:
            raise ShipmentRequestNotFound(str(request_id))
        return request
