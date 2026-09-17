"""Envelope builders for Ordering-owned integration events.

Each builder validates against the registered schema before returning, so an envelope that
would be rejected downstream never reaches the outbox.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ordering.domain.entities import ShipmentRequest
from ordering.domain.value_objects import PaymentTerms, SenderKind
from ordering.infrastructure.contracts.registry import (
    SHIPMENT_CANCELLED_EVENT_TYPE,
    SHIPMENT_CANCELLED_EVENT_VERSION,
    SHIPMENT_REGISTERED_EVENT_TYPE,
    SHIPMENT_REGISTERED_EVENT_VERSION,
    load_ordering_fact_registry,
    validate_envelope,
)

ENVELOPE_VERSION = 1


def _rfc3339(value: datetime) -> str:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _base(
    *,
    event_type: str,
    event_version: int,
    event_id: UUID,
    occurred_at: datetime,
    aggregate_id: UUID,
    aggregate_version: int,
    correlation_id: UUID,
    causation_id: UUID | None,
    traceparent: str | None,
) -> tuple[dict[str, Any], str]:
    contract = load_ordering_fact_registry(event_type, event_version).contract
    envelope: dict[str, Any] = {
        "envelope_version": ENVELOPE_VERSION,
        "event_id": str(event_id),
        "event_type": contract.event_type,
        "event_version": contract.event_version,
        "occurred_at": _rfc3339(occurred_at),
        "producer": contract.producer,
        "message_kind": contract.message_kind,
        "aggregate_scope": contract.aggregate_scope,
        "aggregate_type": contract.aggregate_type,
        "aggregate_id": str(aggregate_id),
        "aggregate_version": aggregate_version,
        "correlation_id": str(correlation_id),
        "causation_id": str(causation_id) if causation_id else None,
        "data_classification": "confidential",
        # The receiver's phone, name and street address never leave Ordering on an event:
        # routing needs the governorate, and consumers that need more ask the API.
        "pii_present": False,
        "schema_uri": contract.schema_uri,
    }
    if traceparent:
        envelope["traceparent"] = traceparent
    return envelope, contract.subject


def build_shipment_registered_envelope(
    *,
    request: ShipmentRequest,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    registered_at = request.registered_at or datetime.now(tz=UTC)
    envelope, subject = _base(
        event_type=SHIPMENT_REGISTERED_EVENT_TYPE,
        event_version=SHIPMENT_REGISTERED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=registered_at,
        aggregate_id=request.request_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    is_cod = request.payment_terms is PaymentTerms.CASH_ON_DELIVERY
    envelope["payload"] = {
        "request_id": str(request.request_id),
        "order_id": str(request.order_id),
        "tracking_code": request.tracking_code,
        "sender_kind": request.sender.kind.value,
        "sender_principal_id": str(request.sender.principal_id),
        "merchant_id": (
            str(request.sender.merchant_id)
            if request.sender.merchant_id is not None
            else None
        ),
        "pickup_store_id": (
            str(request.pickup_store_id) if request.pickup_store_id is not None else None
        ),
        "destination_governorate": request.receiver.governorate,
        "label_code": request.label_code,
        "payment_terms": request.payment_terms.value,
        "cod_amount_minor_units": (
            request.cod_amount.minor_units if is_cod and request.cod_amount else None
        ),
        "cod_currency": (
            request.cod_amount.currency.value if is_cod and request.cod_amount else None
        ),
        # A regular customer's parcel is never collected by a driver (v6.3 p.8, p.15).
        "requires_pickup": request.sender.kind is SenderKind.MERCHANT,
        "open_box_allowed": request.add_ons.open_box_allowed,
        "photo_documentation": request.add_ons.photo_documentation,
        "hudhud_packaging": request.add_ons.hudhud_packaging,
        "delivery_fee_payer": request.add_ons.delivery_fee_payer.value,
        "description": request.description,
        "goods_category_code": request.goods_category_code,
        "weight_grams": request.measurements.weight_grams,
        "registered_at": _rfc3339(registered_at),
    }
    validate_envelope(
        envelope,
        event_type=SHIPMENT_REGISTERED_EVENT_TYPE,
        event_version=SHIPMENT_REGISTERED_EVENT_VERSION,
    )
    return envelope, subject


def build_shipment_cancelled_envelope(
    *,
    request: ShipmentRequest,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    if request.cancellation_reason is None:
        msg = "a cancellation must record why"
        raise ValueError(msg)
    cancelled_at = request.cancelled_at or datetime.now(tz=UTC)
    envelope, subject = _base(
        event_type=SHIPMENT_CANCELLED_EVENT_TYPE,
        event_version=SHIPMENT_CANCELLED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=cancelled_at,
        aggregate_id=request.request_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "request_id": str(request.request_id),
        "order_id": str(request.order_id),
        "tracking_code": request.tracking_code,
        "reason": request.cancellation_reason.value,
        "had_courier_assigned": request.assigned_courier_id is not None,
        "cancelled_at": _rfc3339(cancelled_at),
    }
    validate_envelope(
        envelope,
        event_type=SHIPMENT_CANCELLED_EVENT_TYPE,
        event_version=SHIPMENT_CANCELLED_EVENT_VERSION,
    )
    return envelope, subject
