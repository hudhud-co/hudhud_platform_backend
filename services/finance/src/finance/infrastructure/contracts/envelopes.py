"""Envelope builders for Finance-owned integration facts.

Every builder validates against the registered schema before returning, so an envelope
that would be rejected downstream never reaches the outbox.

What is deliberately absent from every payload: a payout destination (an IBAN or a card
reference is the merchant's), a rejection reason in words (it can name a person or an
investigation), and any receiver identity. Amounts cross the bus as integer minor units
with an explicit currency — never a decimal, never a formatted string.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from finance.domain.entities import CodCollection, PayoutRequest
from finance.domain.money import Money
from finance.domain.value_objects import PayoutStatus
from finance.infrastructure.contracts.registry import (
    CASH_LIMIT_BREACHED_EVENT_TYPE,
    CASH_LIMIT_BREACHED_EVENT_VERSION,
    COD_SETTLED_EVENT_TYPE,
    COD_SETTLED_EVENT_VERSION,
    PAYOUT_DECIDED_EVENT_TYPE,
    PAYOUT_DECIDED_EVENT_VERSION,
    PAYOUT_REQUESTED_EVENT_TYPE,
    PAYOUT_REQUESTED_EVENT_VERSION,
    load_finance_fact_registry,
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
    contract = load_finance_fact_registry(event_type, event_version).contract
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
        # No account number, no destination, no person.
        "pii_present": False,
        "schema_uri": contract.schema_uri,
    }
    if traceparent:
        envelope["traceparent"] = traceparent
    return envelope, contract.subject


def build_cod_settled_envelope(
    *,
    collection: CodCollection,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    """PAY-01 — this parcel's money has actually arrived.

    Refuses to describe an unsettled collection as settled, which is the one mistake
    that would let a merchant be paid for cash still in a driver's pocket.
    """
    if not collection.is_paid or collection.settled_at is None:
        msg = "an unsettled collection is not a settlement"
        raise ValueError(msg)
    if collection.journal_entry_id is None:
        msg = "a settlement must name the entry that recorded it"
        raise ValueError(msg)

    envelope, subject = _base(
        event_type=COD_SETTLED_EVENT_TYPE,
        event_version=COD_SETTLED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=collection.settled_at,
        aggregate_id=collection.collection_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "collection_id": str(collection.collection_id),
        "tracking_code": collection.tracking_code,
        "merchant_id": str(collection.merchant_id),
        "channel": collection.channel.value,
        "goods_minor_units": collection.goods_amount.minor_units,
        "delivery_fee_minor_units": collection.delivery_fee.minor_units,
        "currency": collection.goods_amount.currency.value,
        "settled_at": _rfc3339(collection.settled_at),
        "settling_deposit_id": (
            str(collection.settling_deposit_id)
            if collection.settling_deposit_id
            else None
        ),
        "journal_entry_id": str(collection.journal_entry_id),
    }
    validate_envelope(
        envelope,
        event_type=COD_SETTLED_EVENT_TYPE,
        event_version=COD_SETTLED_EVENT_VERSION,
    )
    return envelope, subject


def build_payout_requested_envelope(
    *,
    payout: PayoutRequest,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    """PAY-06 — a merchant asked to be paid. Where it goes stays in Finance."""
    if payout.requested_at is None:
        msg = "a payout request must record when it was made"
        raise ValueError(msg)

    envelope, subject = _base(
        event_type=PAYOUT_REQUESTED_EVENT_TYPE,
        event_version=PAYOUT_REQUESTED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=payout.requested_at,
        aggregate_id=payout.payout_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "payout_id": str(payout.payout_id),
        "merchant_id": str(payout.merchant_id),
        "method": payout.method.value,
        "amount_minor_units": payout.amount.minor_units,
        "currency": payout.amount.currency.value,
        "requested_at": _rfc3339(payout.requested_at),
        # Whether there is one, never what it is.
        "has_destination": payout.destination_reference is not None,
    }
    validate_envelope(
        envelope,
        event_type=PAYOUT_REQUESTED_EVENT_TYPE,
        event_version=PAYOUT_REQUESTED_EVENT_VERSION,
    )
    return envelope, subject


def build_payout_decided_envelope(
    *,
    payout: PayoutRequest,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Operations approved or rejected it. Paying it out is a separate, blocked step."""
    if payout.status not in {PayoutStatus.APPROVED, PayoutStatus.REJECTED}:
        msg = f"{payout.status.value} is not a decision"
        raise ValueError(msg)
    if payout.decided_at is None:
        msg = "a decided payout must record when it was decided"
        raise ValueError(msg)

    envelope, subject = _base(
        event_type=PAYOUT_DECIDED_EVENT_TYPE,
        event_version=PAYOUT_DECIDED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=payout.decided_at,
        aggregate_id=payout.payout_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "payout_id": str(payout.payout_id),
        "merchant_id": str(payout.merchant_id),
        "status": payout.status.value,
        "amount_minor_units": payout.amount.minor_units,
        "currency": payout.amount.currency.value,
        "decided_at": _rfc3339(payout.decided_at),
        "has_rejection_reason": payout.rejection_reason is not None,
    }
    validate_envelope(
        envelope,
        event_type=PAYOUT_DECIDED_EVENT_TYPE,
        event_version=PAYOUT_DECIDED_EVENT_VERSION,
    )
    return envelope, subject


def build_cash_limit_breached_envelope(
    *,
    driver_principal_id: UUID,
    held: Money,
    limit: Money,
    utilisation_percent: int,
    observed_at: datetime,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    """DRV-A06, OPS-04 — this driver may not be given more COD.

    Refuses to announce a breach that has not happened: a fact that fires below the
    limit would have whoever assigns work stop giving parcels to a driver who is fine.
    """
    if held < limit:
        msg = "this driver is under their limit; there is no breach to announce"
        raise ValueError(msg)
    if limit.is_zero:
        msg = "a limit of zero is not a limit"
        raise ValueError(msg)

    envelope, subject = _base(
        event_type=CASH_LIMIT_BREACHED_EVENT_TYPE,
        event_version=CASH_LIMIT_BREACHED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=observed_at,
        aggregate_id=driver_principal_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "driver_principal_id": str(driver_principal_id),
        "held_minor_units": held.minor_units,
        "limit_minor_units": limit.minor_units,
        "currency": held.currency.value,
        "utilisation_percent": utilisation_percent,
        "observed_at": _rfc3339(observed_at),
    }
    validate_envelope(
        envelope,
        event_type=CASH_LIMIT_BREACHED_EVENT_TYPE,
        event_version=CASH_LIMIT_BREACHED_EVENT_VERSION,
    )
    return envelope, subject
