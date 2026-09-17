"""Editing and cancelling a shipment (SHP-10, SHP-11, SHP-12).

The rule this service encodes is not "who is allowed to edit" but "what is still true".
A parcel nobody has collected can be changed freely. A parcel a courier is booked for can
be changed, but moving where or when they should come releases the courier — the app is
explicit: "The courier has been released. Confirm your changes and we book a new one."
A parcel already moving in the network can only be corrected by support, and only for the
things that can still be acted on before the door.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ordering.domain.entities import ShipmentRequest
from ordering.domain.errors import (
    CodAmountNotAllowed,
    CodAmountTooLateToChange,
    FieldNotEditableAtThisStage,
    InvalidPhoneNumber,
    RequestAlreadyInCustody,
    SenderMayNotSetPlatformPolicy,
    ShipmentRequestNotFound,
    UnknownGovernorate,
)
from ordering.domain.messaging import OutboxRecord, OutboxStatus
from ordering.domain.money import Money
from ordering.domain.value_objects import (
    COURIER_RELEASING_FIELDS,
    SUPPORT_CORRECTABLE_IN_CUSTODY,
    CancellationReason,
    EditStage,
    PaymentTerms,
    ReceiverDetails,
    RequestStatus,
    ShipmentAddOns,
    normalize_governorate,
    normalize_phone,
)
from ordering.infrastructure.contracts.envelopes import (
    build_shipment_cancelled_envelope,
)
from ordering.ports.repository import OrderingUnitOfWork

#: v6.3 p.13 "Role boundary — Sender" (MER-15). A sender decides commercial terms and
#: add-ons; these are fixed company-wide and are refused rather than stored.
PLATFORM_FIXED_KEYS: frozenset[str] = frozenset(
    {
        "acceptance_standard",
        "packaging_standard",
        "return_fee_owner",
        "refund_owner",
        "hold_period_days",
        "door_wait_minutes",
        "liability_position",
    }
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class AmendmentOutcome:
    request: ShipmentRequest
    #: True when the change released a booked courier and a new one must be found.
    courier_released: bool
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReceiverAmendment:
    phone: str | None = None
    governorate: str | None = None
    name: str | None = None
    address_line: str | None = None
    landmark: str | None = None

    @property
    def is_empty(self) -> bool:
        return all(
            getattr(self, name) is None
            for name in ("phone", "governorate", "name", "address_line", "landmark")
        )


class AmendmentService:
    def __init__(
        self, unit_of_work: OrderingUnitOfWork, *, outbox_max_attempts: int = 5
    ) -> None:
        self._uow = unit_of_work
        self._outbox_max_attempts = outbox_max_attempts

    # ------------------------------------------------------------- read

    def edit_stage(self, *, request_id: UUID) -> EditStage:
        self._uow.begin()
        try:
            request = self._load(request_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request.edit_stage

    def editable_fields(self, *, request_id: UUID) -> frozenset[str]:
        self._uow.begin()
        try:
            request = self._load(request_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request.self_service_editable_fields()

    # ------------------------------------------------------------- self-service

    def amend(
        self,
        *,
        request_id: UUID,
        receiver: ReceiverAmendment | None = None,
        description: str | None = None,
        goods_category_code: str | None = None,
        add_ons: ShipmentAddOns | None = None,
        pickup_store_id: UUID | None = None,
        pickup_window_start: datetime | None = None,
        pickup_window_end: datetime | None = None,
        rejected_keys: tuple[str, ...] = (),
    ) -> AmendmentOutcome:
        """A sender changing their own shipment.

        Every requested field is checked against the stage *before* anything is written,
        so a request that mixes an allowed change with a disallowed one changes nothing.
        """
        forbidden = tuple(sorted(key for key in rejected_keys if key in PLATFORM_FIXED_KEYS))
        if forbidden:
            raise SenderMayNotSetPlatformPolicy(forbidden)

        requested: list[str] = []
        if receiver is not None and not receiver.is_empty:
            requested.append("receiver")
        if description is not None:
            requested.append("description")
        if goods_category_code is not None:
            requested.append("goods_category")
        if add_ons is not None:
            requested.append("add_ons")
        if pickup_store_id is not None:
            requested.append("pickup_address")
        if pickup_window_start is not None or pickup_window_end is not None:
            requested.append("pickup_window")

        self._uow.begin()
        try:
            request = self._load(request_id)
            stage = request.edit_stage
            for name in requested:
                if not request.may_edit(name):
                    raise FieldNotEditableAtThisStage(name, stage.value)

            if receiver is not None and not receiver.is_empty:
                request.receiver = self._merge_receiver(request.receiver, receiver)
            if description is not None:
                request.description = description.strip()
            if goods_category_code is not None:
                request.goods_category_code = goods_category_code
            if add_ons is not None:
                request.add_ons = add_ons
            if pickup_store_id is not None:
                request.pickup_store_id = pickup_store_id
            if pickup_window_start is not None:
                request.pickup_window_start = pickup_window_start
            if pickup_window_end is not None:
                request.pickup_window_end = pickup_window_end

            released = False
            if (
                stage is EditStage.COURIER_ASSIGNED
                and set(requested) & COURIER_RELEASING_FIELDS
            ):
                # "The courier has been released. Confirm your changes and we book a new
                # one." Keeping the booking would send a driver to the old address.
                request.assigned_courier_id = None
                released = True

            request.version += 1
            self._uow.requests.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return AmendmentOutcome(
            request=request, courier_released=released, changed_fields=tuple(requested)
        )

    # ------------------------------------------------------------- COD

    def change_cod_amount(self, *, request_id: UUID, amount: Money) -> ShipmentRequest:
        """SHP-12.

        The amount can be corrected while the courier can still be told. Once they have
        reached the receiver the old amount stands and the difference is settled through a
        claim — so this refuses rather than rewriting history.
        """
        self._uow.begin()
        try:
            request = self._load(request_id)
            if request.payment_terms is not PaymentTerms.CASH_ON_DELIVERY:
                raise CodAmountNotAllowed(request.payment_terms.value)
            if request.edit_stage is EditStage.CLOSED or request.courier_at_the_door:
                raise CodAmountTooLateToChange()
            request.cod_amount = amount
            request.version += 1
            self._uow.requests.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    # ------------------------------------------------------------- support

    def apply_support_correction(
        self,
        *,
        request_id: UUID,
        receiver: ReceiverAmendment | None = None,
        cod_amount: Money | None = None,
    ) -> ShipmentRequest:
        """"Support applies it before the delivery attempt."

        A narrower door than self-service, deliberately: contents and dimensions are facts
        about a box someone is already carrying and cannot be corrected by typing.
        """
        requested = []
        if receiver is not None and not receiver.is_empty:
            requested.append("receiver")
        if cod_amount is not None:
            requested.append("cod_amount")

        self._uow.begin()
        try:
            request = self._load(request_id)
            if request.edit_stage is EditStage.CLOSED:
                raise FieldNotEditableAtThisStage(
                    ", ".join(requested) or "shipment", request.edit_stage.value
                )
            for name in requested:
                if name not in SUPPORT_CORRECTABLE_IN_CUSTODY:
                    raise FieldNotEditableAtThisStage(name, request.edit_stage.value)
            if cod_amount is not None:
                if request.payment_terms is not PaymentTerms.CASH_ON_DELIVERY:
                    raise CodAmountNotAllowed(request.payment_terms.value)
                if request.courier_at_the_door:
                    raise CodAmountTooLateToChange()
                request.cod_amount = cod_amount
            if receiver is not None and not receiver.is_empty:
                request.receiver = self._merge_receiver(request.receiver, receiver)
            request.version += 1
            self._uow.requests.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    # ------------------------------------------------------------- cancellation

    def cancel(
        self, *, request_id: UUID, reason: CancellationReason
    ) -> tuple[ShipmentRequest, UUID]:
        """SHP-10 — cancellable only while nothing has entered custody.

        A parcel Hudhud is already carrying is Shipment's to close; cancelling it here
        would leave a box in a van that no context believes exists.
        """
        self._uow.begin()
        try:
            request = self._load(request_id)
            if request.custody_started_at is not None:
                raise RequestAlreadyInCustody()
            if not request.can_transition_to(RequestStatus.CANCELLED):
                raise RequestAlreadyInCustody()

            request.status = RequestStatus.CANCELLED
            request.cancelled_at = _now()
            request.cancellation_reason = reason
            request.version += 1
            self._uow.requests.save(request)

            event_id = uuid4()
            payload_json, subject = build_shipment_cancelled_envelope(
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

    # ------------------------------------------------------------- internals

    def _load(self, request_id: UUID) -> ShipmentRequest:
        request = self._uow.requests.get(request_id)
        if request is None:
            raise ShipmentRequestNotFound(str(request_id))
        return request

    @staticmethod
    def _merge_receiver(
        current: ReceiverDetails, amendment: ReceiverAmendment
    ) -> ReceiverDetails:
        phone = current.phone
        if amendment.phone is not None:
            try:
                phone = normalize_phone(amendment.phone)
            except ValueError as exc:
                raise InvalidPhoneNumber(amendment.phone) from exc
        governorate = current.governorate
        if amendment.governorate is not None:
            try:
                governorate = normalize_governorate(amendment.governorate)
            except ValueError as exc:
                raise UnknownGovernorate(amendment.governorate) from exc
        return ReceiverDetails(
            phone=phone,
            governorate=governorate,
            name=(
                (amendment.name or "").strip() or None
                if amendment.name is not None
                else current.name
            ),
            address_line=(
                (amendment.address_line or "").strip() or None
                if amendment.address_line is not None
                else current.address_line
            ),
            landmark=(
                (amendment.landmark or "").strip() or None
                if amendment.landmark is not None
                else current.landmark
            ),
            geo=current.geo,
        )
