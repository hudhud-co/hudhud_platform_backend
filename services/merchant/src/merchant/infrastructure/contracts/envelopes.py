"""Envelope builders for Merchant-owned integration events.

Each builder validates against the registered schema before returning, so an envelope that
would be rejected downstream never reaches the outbox in the first place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from merchant.domain.entities import MerchantApplication, TeamMembership
from merchant.domain.value_objects import ApplicationStatus
from merchant.infrastructure.contracts.registry import (
    APPLICATION_DECIDED_EVENT_TYPE,
    APPLICATION_DECIDED_EVENT_VERSION,
    TEAM_MEMBERSHIP_CHANGED_EVENT_TYPE,
    TEAM_MEMBERSHIP_CHANGED_EVENT_VERSION,
    load_merchant_fact_registry,
    validate_envelope,
)

ENVELOPE_VERSION = 1

_DECISION_BY_STATUS = {
    ApplicationStatus.APPROVED: "APPROVED",
    ApplicationStatus.CHANGES_REQUESTED: "CHANGES_REQUESTED",
}


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
    loaded = load_merchant_fact_registry(event_type, event_version)
    contract = loaded.contract
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
        # Merchant facts deliberately carry identifiers and decisions only. The business
        # details an applicant supplies, the review reason and an invitee's phone number
        # all stay behind Merchant's API.
        "pii_present": False,
        "schema_uri": contract.schema_uri,
    }
    if traceparent:
        envelope["traceparent"] = traceparent
    return envelope, contract.subject


def build_application_decided_envelope(
    *,
    application: MerchantApplication,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    merchant_id: UUID | None = None,
    merchant_code: str | None = None,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    decision = _DECISION_BY_STATUS.get(application.status)
    if decision is None:
        msg = f"{application.status} is not a decision outcome"
        raise ValueError(msg)
    decided_at = application.decided_at or datetime.now(tz=UTC)

    envelope, subject = _base(
        event_type=APPLICATION_DECIDED_EVENT_TYPE,
        event_version=APPLICATION_DECIDED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=decided_at,
        aggregate_id=application.application_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    payload: dict[str, Any] = {
        "application_id": str(application.application_id),
        "applicant_principal_id": str(application.applicant_principal_id),
        "decision": decision,
        "decided_at": _rfc3339(decided_at),
    }
    if decision == "APPROVED":
        if merchant_id is None or merchant_code is None:
            msg = "an approval must name the merchant it created"
            raise ValueError(msg)
        payload["merchant_id"] = str(merchant_id)
        payload["merchant_code"] = merchant_code
    envelope["payload"] = payload

    validate_envelope(
        envelope,
        event_type=APPLICATION_DECIDED_EVENT_TYPE,
        event_version=APPLICATION_DECIDED_EVENT_VERSION,
    )
    return envelope, subject


def build_team_membership_changed_envelope(
    *,
    membership: TeamMembership,
    merchant_aggregate_version: int,
    changed_at: datetime,
    event_id: UUID,
    correlation_id: UUID,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    envelope, subject = _base(
        event_type=TEAM_MEMBERSHIP_CHANGED_EVENT_TYPE,
        event_version=TEAM_MEMBERSHIP_CHANGED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=changed_at,
        aggregate_id=membership.merchant_id,
        aggregate_version=merchant_aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "membership_id": str(membership.membership_id),
        "merchant_id": str(membership.merchant_id),
        "status": membership.status.value,
        "role": membership.role.value,
        "member_principal_id": (
            str(membership.member_principal_id)
            if membership.member_principal_id is not None
            else None
        ),
        "store_ids": [str(store_id) for store_id in membership.store_ids],
        "changed_at": _rfc3339(changed_at),
    }
    validate_envelope(
        envelope,
        event_type=TEAM_MEMBERSHIP_CHANGED_EVENT_TYPE,
        event_version=TEAM_MEMBERSHIP_CHANGED_EVENT_VERSION,
    )
    return envelope, subject
