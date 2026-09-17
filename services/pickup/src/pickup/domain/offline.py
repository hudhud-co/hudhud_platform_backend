"""Offline work authorization, append-only command queue, and reconciliation cases.

A driver may only capture work offline for a resource it downloaded under a signed,
time-boxed authorization. Replayed commands are applied through the same application
services as online commands — the queue never writes domain state directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class OfflineResourceType(StrEnum):
    PICKUP_TASK = "PICKUP_TASK"


class OfflineOperation(StrEnum):
    """Pickup operations a driver may capture while offline."""

    ARRIVE = "ARRIVE"
    SCAN = "SCAN"
    CAPTURE_PROOF = "CAPTURE_PROOF"
    ACCEPT_CUSTODY = "ACCEPT_CUSTODY"
    REPORT_EXCEPTION = "REPORT_EXCEPTION"
    FAIL = "FAIL"


# Operations whose authority depends on a server-issued credential or sender ceremony
# that cannot be produced offline. They are preserved, never silently applied.
DEFERRED_OFFLINE_OPERATIONS: frozenset[OfflineOperation] = frozenset(
    {OfflineOperation.ACCEPT_CUSTODY}
)

# Operations that move or release physical custody — reconciliation cases opened for
# these are flagged so Operations sees the custody implication.
CUSTODY_OFFLINE_OPERATIONS: frozenset[OfflineOperation] = frozenset(
    {OfflineOperation.ACCEPT_CUSTODY, OfflineOperation.FAIL}
)


class OfflineEventStatus(StrEnum):
    APPLIED = "APPLIED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    REJECTED = "REJECTED"


class OfflineOutcomeCode(StrEnum):
    APPLIED = "APPLIED"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    SEQUENCE_REUSE = "SEQUENCE_REUSE"
    OPERATION_ID_REUSE = "OPERATION_ID_REUSE"
    PAYLOAD_FINGERPRINT_MISMATCH = "PAYLOAD_FINGERPRINT_MISMATCH"
    AUTHORIZATION_SCOPE_MISMATCH = "AUTHORIZATION_SCOPE_MISMATCH"
    OPERATION_NOT_AUTHORIZED = "OPERATION_NOT_AUTHORIZED"
    CAPTURE_BEFORE_AUTHORIZATION = "CAPTURE_BEFORE_AUTHORIZATION"
    CAPTURE_AFTER_AUTHORIZATION_EXPIRY = "CAPTURE_AFTER_AUTHORIZATION_EXPIRY"
    CAPTURE_TIME_IN_FUTURE = "CAPTURE_TIME_IN_FUTURE"
    ASSIGNMENT_NO_LONGER_OWNED = "ASSIGNMENT_NO_LONGER_OWNED"
    STALE_ASSIGNMENT_REVISION = "STALE_ASSIGNMENT_REVISION"
    OWNING_WORKFLOW_REQUIRED = "OWNING_WORKFLOW_REQUIRED"
    DOMAIN_REJECTED = "DOMAIN_REJECTED"


class ReconciliationCaseStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ReconciliationResolution(StrEnum):
    ORDER_RESTORED_AND_APPLIED = "ORDER_RESTORED_AND_APPLIED"
    SERVER_STATE_AUTHORITATIVE = "SERVER_STATE_AUTHORITATIVE"
    MANUAL_CORRECTION_RECORDED = "MANUAL_CORRECTION_RECORDED"
    DISMISSED_NO_ACTION = "DISMISSED_NO_ACTION"


@dataclass(slots=True)
class OfflineAuthorization:
    """Signed, device-bound, time-boxed authority to work one resource offline."""

    authorization_id: UUID
    driver_user_id: str
    device_id_hash: str
    resource_type: OfflineResourceType
    resource_id: UUID
    assignment_revision: str
    permitted_operations: tuple[OfflineOperation, ...]
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    sync_deadline: datetime
    resource_snapshot: dict[str, Any] = field(default_factory=dict)
    revoked_at: datetime | None = None
    revoked_reason: str | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


@dataclass(slots=True)
class OfflineStream:
    """Per-device append-only capture stream with contiguous sequencing."""

    stream_id: UUID
    authorization_id: UUID
    driver_user_id: str
    device_id_hash: str
    last_contiguous_sequence: int = 0
    last_received_at: datetime | None = None


@dataclass(slots=True)
class OfflineEventRecord:
    """Preserved offline capture — every submission is kept, applied or not."""

    event_row_id: UUID
    stream_id: UUID
    authorization_id: UUID
    driver_user_id: str
    operation_id: UUID
    sequence: int
    operation: OfflineOperation
    resource_type: OfflineResourceType
    resource_id: UUID
    assignment_revision: str
    captured_at: datetime
    payload_fingerprint: str
    payload: dict[str, Any]
    status: OfflineEventStatus
    outcome_code: OfflineOutcomeCode
    outcome: dict[str, Any]
    processed_at: datetime
    replayed: bool = False


@dataclass(slots=True)
class OfflineReconciliationCase:
    """Operations-owned case for an offline capture the server could not apply."""

    case_id: UUID
    offline_event_row_id: UUID
    driver_user_id: str
    resource_type: OfflineResourceType
    resource_id: UUID
    status: ReconciliationCaseStatus
    reason_code: OfflineOutcomeCode
    reason_detail: str
    authoritative_state: dict[str, Any]
    submitted_event: dict[str, Any]
    custody_implication: bool
    opened_at: datetime
    resolved_at: datetime | None = None
    resolved_by_user_id: str | None = None
    resolution: ReconciliationResolution | None = None
    resolution_notes: str | None = None
