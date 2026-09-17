"""Envelope builders for Delivery-owned integration events.

Every builder validates against the registered schema before returning, so an envelope
that would be rejected downstream never reaches the outbox.

Three things are deliberately absent from every payload: the delivery code, any ID
imagery, and the receiver's contact details. Consumers that need more ask Delivery's API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from delivery.domain.entities import DeliveryStop, FailedAttempt, PaymentRecord
from delivery.domain.value_objects import (
    InspectionOutcome,
    PaymentMethod,
    SealCheckOutcome,
)
from delivery.infrastructure.contracts.registry import (
    ATTEMPT_FAILED_EVENT_TYPE,
    ATTEMPT_FAILED_EVENT_VERSION,
    COD_COLLECTED_EVENT_TYPE,
    COD_COLLECTED_EVENT_VERSION,
    DELIVERED_EVENT_TYPE,
    DELIVERED_EVENT_VERSION,
    DEPARTED_EVENT_TYPE,
    DEPARTED_EVENT_VERSION,
    load_delivery_fact_registry,
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
    contract = load_delivery_fact_registry(event_type, event_version).contract
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
        # No delivery code, no ID imagery, no receiver contact detail leaves this service.
        "pii_present": False,
        "schema_uri": contract.schema_uri,
    }
    if traceparent:
        envelope["traceparent"] = traceparent
    return envelope, contract.subject


def build_delivered_envelope(
    *,
    stop: DeliveryStop,
    payment: PaymentRecord | None,
    photo_count: int,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    if stop.verified_by_method is None or stop.delivered_at is None:
        msg = "a delivered stop must record how it was verified and when"
        raise ValueError(msg)
    if stop.inspection_outcome is InspectionOutcome.OPEN_BOX_REFUSED:
        msg = "a refusal is not a delivery"
        raise ValueError(msg)

    envelope, subject = _base(
        event_type=DELIVERED_EVENT_TYPE,
        event_version=DELIVERED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=stop.delivered_at,
        aggregate_id=stop.stop_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    method = payment.method if payment is not None else PaymentMethod.PREPAID
    collected = (
        payment.amount
        if payment is not None and method is not PaymentMethod.PREPAID
        else None
    )
    envelope["payload"] = {
        "stop_id": str(stop.stop_id),
        "tracking_code": stop.tracking_code,
        "delivered_at": _rfc3339(stop.delivered_at),
        "delivering_driver_id": str(stop.driver_principal_id),
        "verified_by": stop.verified_by_method.value,
        "inspection_outcome": (
            stop.inspection_outcome.value
            if stop.inspection_outcome is not None
            else InspectionOutcome.SEALED_ACCEPTED.value
        ),
        "seal_outcome": (
            stop.seal_outcome.value
            if stop.seal_outcome is not None
            else SealCheckOutcome.NOT_APPLICABLE.value
        ),
        "payment_method": method.value,
        "amount_collected_minor_units": collected.minor_units if collected else None,
        "amount_currency": collected.currency.value if collected else None,
        "photo_media_count": photo_count,
    }
    validate_envelope(
        envelope, event_type=DELIVERED_EVENT_TYPE, event_version=DELIVERED_EVENT_VERSION
    )
    return envelope, subject


def build_attempt_failed_envelope(
    *,
    stop: DeliveryStop,
    attempt: FailedAttempt,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    envelope, subject = _base(
        event_type=ATTEMPT_FAILED_EVENT_TYPE,
        event_version=ATTEMPT_FAILED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=attempt.recorded_at,
        aggregate_id=stop.stop_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "stop_id": str(stop.stop_id),
        "tracking_code": stop.tracking_code,
        "reason": attempt.reason.value,
        "refusal_reason": (
            stop.refusal_reason.value if stop.refusal_reason is not None else None
        ),
        # v6.3 p.34 — nothing is collected on a refusal or a failed attempt.
        "amount_collected_minor_units": 0,
        "recorded_at": _rfc3339(attempt.recorded_at),
        "recorded_by_driver_id": str(attempt.recorded_by_actor_id),
        "parcel_remains_in_hudhud_custody": True,
    }
    validate_envelope(
        envelope,
        event_type=ATTEMPT_FAILED_EVENT_TYPE,
        event_version=ATTEMPT_FAILED_EVENT_VERSION,
    )
    return envelope, subject


def build_cod_collected_envelope(
    *,
    stop: DeliveryStop,
    payment: PaymentRecord,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    if payment.amount is None:
        msg = "a collected payment must carry its amount"
        raise ValueError(msg)
    if payment.method is PaymentMethod.PREPAID:
        msg = "a prepaid shipment collects nothing at the door"
        raise ValueError(msg)

    envelope, subject = _base(
        event_type=COD_COLLECTED_EVENT_TYPE,
        event_version=COD_COLLECTED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=payment.recorded_at or datetime.now(tz=UTC),
        aggregate_id=stop.stop_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "stop_id": str(stop.stop_id),
        "tracking_code": stop.tracking_code,
        "method": payment.method.value,
        "amount_minor_units": payment.amount.minor_units,
        "currency": payment.amount.currency.value,
        "pos_reference": payment.pos_reference,
        "pos_receipt_captured": payment.pos_receipt is not None,
        "collected_at": _rfc3339(payment.recorded_at or datetime.now(tz=UTC)),
        "collecting_driver_id": str(stop.driver_principal_id),
        "enters_driver_cash_custody": payment.enters_driver_cash_custody,
    }
    validate_envelope(
        envelope,
        event_type=COD_COLLECTED_EVENT_TYPE,
        event_version=COD_COLLECTED_EVENT_VERSION,
    )
    return envelope, subject


def build_departed_envelope(
    *,
    stop: DeliveryStop,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    eta_from_minutes: int | None = None,
    eta_to_minutes: int | None = None,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    if stop.departed_at is None:
        msg = "a departure must record when the driver set off"
        raise ValueError(msg)

    envelope, subject = _base(
        event_type=DEPARTED_EVENT_TYPE,
        event_version=DEPARTED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=stop.departed_at,
        aggregate_id=stop.stop_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "stop_id": str(stop.stop_id),
        "tracking_code": stop.tracking_code,
        "departed_at": _rfc3339(stop.departed_at),
        "driver_id": str(stop.driver_principal_id),
        "eta_window_minutes_from": eta_from_minutes,
        "eta_window_minutes_to": eta_to_minutes,
    }
    validate_envelope(
        envelope, event_type=DEPARTED_EVENT_TYPE, event_version=DEPARTED_EVENT_VERSION
    )
    return envelope, subject
