"""Sender→driver handover verification and driver→hub handover manifests.

Two distinct custody ceremonies live here:

1. **Courier verification** — the sender proves it handed the parcel to the *assigned*
   courier (dynamic challenge) and confirms the parcel manifest. This gates acceptance.
2. **Hub handover manifest** — the driver releases custody to an origin hub. Pickup owns
   the driver-side manifest; the canonical Shipment custody change is applied by Shipment
   from `pickup.fact.handover_completed` (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class SenderType(StrEnum):
    MERCHANT = "MERCHANT"
    CUSTOMER_DIRECT = "CUSTOMER_DIRECT"


class VerificationMethod(StrEnum):
    DYNAMIC_CHALLENGE_V1 = "DYNAMIC_CHALLENGE_V1"


class CourierChallengeStatus(StrEnum):
    ISSUED = "ISSUED"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"


class CourierManifestStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"


class VerificationInvalidationReason(StrEnum):
    REISSUED = "REISSUED"
    REASSIGNED = "REASSIGNED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_SUPERSEDED = "TASK_SUPERSEDED"
    MANIFEST_MUTATED = "MANIFEST_MUTATED"
    ACCEPTANCE_CONSUMED = "ACCEPTANCE_CONSUMED"


class HandoverManifestStatus(StrEnum):
    """Driver-side hub handover manifest lifecycle."""

    READY = "READY"
    ARRIVED_AT_HUB = "ARRIVED_AT_HUB"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_DISCREPANCIES = "COMPLETED_WITH_DISCREPANCIES"
    CANCELLED = "CANCELLED"


class HandoverManifestItemStatus(StrEnum):
    EXPECTED = "EXPECTED"
    RECEIVED = "RECEIVED"
    MISSING = "MISSING"
    DISCREPANCY = "DISCREPANCY"


class HandoverDiscrepancyReason(StrEnum):
    MISSING_FROM_DRIVER = "MISSING_FROM_DRIVER"
    DAMAGED_AT_HANDOVER = "DAMAGED_AT_HANDOVER"
    WRONG_HUB_RECEIVED = "WRONG_HUB_RECEIVED"
    OTHER = "OTHER"


ACTIVE_HANDOVER_MANIFEST_STATUSES: frozenset[HandoverManifestStatus] = frozenset(
    {
        HandoverManifestStatus.READY,
        HandoverManifestStatus.ARRIVED_AT_HUB,
        HandoverManifestStatus.IN_PROGRESS,
    }
)

TERMINAL_HANDOVER_MANIFEST_STATUSES: frozenset[HandoverManifestStatus] = frozenset(
    {
        HandoverManifestStatus.COMPLETED,
        HandoverManifestStatus.COMPLETED_WITH_DISCREPANCIES,
        HandoverManifestStatus.CANCELLED,
    }
)

# An item still bound to the driver's custody.
OPEN_HANDOVER_ITEM_STATUSES: frozenset[HandoverManifestItemStatus] = frozenset(
    {HandoverManifestItemStatus.EXPECTED, HandoverManifestItemStatus.MISSING}
)


@dataclass(slots=True)
class CourierChallenge:
    """Assignment-bound sender verification challenge. Raw secret is never persisted."""

    challenge_id: UUID
    pickup_task_id: UUID
    shipment_id: UUID
    assigned_driver_user_id: str
    sender_type: SenderType
    assignment_fingerprint: str
    secret_hash: str
    method: VerificationMethod
    status: CourierChallengeStatus
    issued_by_user_id: str
    issued_at: datetime
    expires_at: datetime
    failed_attempt_count: int = 0
    locked_until: datetime | None = None
    verified_at: datetime | None = None
    verification_valid_until: datetime | None = None
    verifier_user_id: str | None = None
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: VerificationInvalidationReason | None = None

    @property
    def is_open(self) -> bool:
        return self.status in (
            CourierChallengeStatus.ISSUED,
            CourierChallengeStatus.VERIFIED,
        )

    @property
    def is_live_verified(self) -> bool:
        return (
            self.status is CourierChallengeStatus.VERIFIED
            and self.consumed_at is None
            and self.invalidated_at is None
        )


@dataclass(slots=True)
class CourierManifest:
    """Parcel manifest the courier submits and the sender confirms."""

    manifest_id: UUID
    pickup_task_id: UUID
    shipment_id: UUID
    manifest_digest: str
    status: CourierManifestStatus
    submitted_by_user_id: str
    submitted_at: datetime
    confirmed_by_user_id: str | None = None
    confirmed_at: datetime | None = None
    confirmation_valid_until: datetime | None = None
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: VerificationInvalidationReason | None = None

    @property
    def is_open(self) -> bool:
        return self.status in (
            CourierManifestStatus.SUBMITTED,
            CourierManifestStatus.CONFIRMED,
        )


@dataclass(slots=True)
class HandoverManifest:
    """Driver-declared set of parcels being released to one origin hub."""

    manifest_id: UUID
    manifest_code: str
    driver_user_id: str
    hub_id: UUID
    status: HandoverManifestStatus
    expected_count: int
    received_count: int
    missing_count: int
    discrepancy_count: int
    created_at: datetime
    ready_at: datetime | None = None
    arrived_at_hub_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_HANDOVER_MANIFEST_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_HANDOVER_MANIFEST_STATUSES


@dataclass(slots=True)
class HandoverManifestItem:
    """One shipment line on a hub handover manifest."""

    item_id: UUID
    manifest_id: UUID
    pickup_task_id: UUID
    shipment_id: UUID
    status: HandoverManifestItemStatus
    added_at: datetime
    received_at: datetime | None = None
    received_by_user_id: str | None = None
    received_hub_id: UUID | None = None
    discrepancy_reason: HandoverDiscrepancyReason | None = None
    notes: str | None = None

    @property
    def releases_custody(self) -> bool:
        """True once the parcel is physically with the hub, cleanly or in dispute."""
        return self.status in (
            HandoverManifestItemStatus.RECEIVED,
            HandoverManifestItemStatus.DISCREPANCY,
        )
