"""HTTP schemas for driver workforce, handover ceremony, hub handover, and offline work.

No request body ever carries actor identity: the authenticated actor comes from the
authorization adapter. Responses never echo a challenge secret or an offline token
beyond the single issue response that creates it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


# ------------------------------------------------------------------- work session


class StartWorkSessionRequest(_Frozen):
    home_hub_id: UUID | None = None


class PauseWorkSessionRequest(_Frozen):
    reason: str = Field(min_length=1, max_length=32)
    notes: str | None = Field(default=None, max_length=512)


class EndWorkSessionRequest(_Frozen):
    reason: str = Field(min_length=1, max_length=32)
    notes: str | None = Field(default=None, max_length=512)


class WorkloadResponse(_Frozen):
    open_custody_shipment_ids: list[UUID]
    active_handover_manifest_ids: list[UUID]
    active_task_ids: list[UUID]
    unsynced_offline_operation_ids: list[UUID]
    end_blockers: list[str]
    pause_blockers: list[str]


class WorkSessionResponse(_Frozen):
    driver_user_id: str
    capability: str
    status: str
    availability: str
    #: False only on `GET /work-sessions/current`, and only when the driver has no open
    #: session. `session_id` and `started_at` are then null rather than invented, so a
    #: client never has to recognise a sentinel to tell "offline" from a real session.
    #: Every command response describes a session that exists, and leaves this True.
    has_open_session: bool = True
    session_id: UUID | None = None
    started_at: datetime | None = None
    home_hub_id: UUID | None
    paused_at: datetime | None
    resumed_at: datetime | None
    ended_at: datetime | None
    pause_reason: str | None
    end_reason: str | None
    version: int
    workload: WorkloadResponse
    idempotent_replay: bool = False


# --------------------------------------------------------------- task lifecycle


class DeclineAssignmentRequest(_Frozen):
    reason: str = Field(min_length=1, max_length=32)
    notes: str | None = Field(default=None, max_length=1024)


class ScanTaskRequest(_Frozen):
    scanned_identifier: str = Field(min_length=1, max_length=256)


class CaptureProofRequest(_Frozen):
    package_condition_status: str = Field(min_length=1, max_length=32)
    condition_notes: str | None = Field(default=None, max_length=1024)
    evidence_present: bool = False
    # Optional so clients that predate the packaging judgement keep working unchanged.
    packaging_assessment: str | None = Field(default=None, max_length=32)
    decision: str | None = Field(default=None, max_length=32)


class ReportExceptionRequest(_Frozen):
    reason: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=1024)
    contact_attempted: bool = False
    contact_attempt_count: int = Field(default=0, ge=0, le=50)


class FailTaskRequest(_Frozen):
    reason: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=1024)


class TaskLifecycleResponse(_Frozen):
    pickup_task_id: UUID
    shipment_id: UUID
    status: str
    assignment_state: str
    acceptance_state: str | None
    assignment_revision: str
    has_pickup_condition_proof: bool
    version: int
    idempotent_replay: bool = False
    packaging_assessment: str | None = None
    condition_decision: str | None = None
    stop_outcome: str | None = None
    stop_outcome_reason: str | None = None


class RefusePickupRequest(_Frozen):
    reason: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=1024)


class ResolveScanRequest(_Frozen):
    scanned_identifier: str = Field(default="", max_length=256)
    unreadable: bool = False


class ScanResolutionResponse(_Frozen):
    outcome: str
    can_proceed: bool
    pickup_task_id: UUID | None = None
    shipment_id: UUID | None = None


class StopReadinessResponse(_Frozen):
    assigned_batch_id: UUID
    expected: int
    accepted: int
    refused: int
    not_presented: int
    closed_by_recovery: int
    unresolved: int
    can_complete: bool
    is_partial: bool
    blocking_reason: str | None = None


class AcceptanceStatusResponse(_Frozen):
    pickup_task_id: UUID
    recorded: bool
    safe_to_retry: bool
    acceptance_state: str | None = None
    accepted_at: datetime | None = None
    accepted_by_driver_user_id: str | None = None


class AcceptCustodyRequest(_Frozen):
    scanned_identifier: str = Field(min_length=1, max_length=256)
    outcome: str = Field(default="ACCEPTED", min_length=1, max_length=32)
    media_refs: list[MediaRefRequest] = Field(default_factory=list, max_length=20)
    correlation_id: UUID | None = None


class AcceptCustodyResponse(_Frozen):
    pickup_task_id: UUID
    shipment_id: UUID
    acceptance_state: str
    accepted_at: datetime | None
    accepted_by_driver_user_id: str | None
    event_id: UUID
    aggregate_version: int
    idempotent_replay: bool = False


class TaskHistoryEntryResponse(_Frozen):
    history_id: UUID
    pickup_task_id: UUID
    action: str
    actor_id: str
    actor_role: str
    previous_status: str | None
    new_status: str | None
    occurred_at: datetime
    request_id: str | None
    details: dict


class TaskHistoryResponse(_Frozen):
    pickup_task_id: UUID
    entries: list[TaskHistoryEntryResponse]


# ------------------------------------------------------------ handover ceremony


class IssuedChallengeResponse(_Frozen):
    """The only response that ever carries the challenge secret, and only once."""

    challenge_id: UUID
    pickup_task_id: UUID
    challenge_payload: str
    expires_at: datetime
    ttl_seconds: int
    status: str
    reissued: bool


class VerifyChallengeRequest(_Frozen):
    presented_payload: str = Field(min_length=1, max_length=512)
    challenge_id: UUID | None = None


class VerificationResponse(_Frozen):
    challenge_id: UUID
    pickup_task_id: UUID
    status: str
    courier_user_id: str
    verified_at: datetime
    valid_until: datetime
    idempotent_replay: bool = False


class HandoverDiscoveryResponse(_Frozen):
    """Ceremony readiness for one shipment. Informational only — never authority."""

    shipment_id: UUID
    pickup_task_id: UUID
    state: str
    verification_required: bool
    actionable: bool
    can_verify_courier: bool
    can_confirm_manifest: bool


class SubmitCourierManifestRequest(_Frozen):
    scanned_identifier: str = Field(min_length=1, max_length=256)


class ConfirmCourierManifestRequest(_Frozen):
    expected_manifest_digest: str | None = Field(default=None, min_length=64, max_length=64)


class CourierManifestResponse(_Frozen):
    manifest_id: UUID
    pickup_task_id: UUID
    shipment_id: UUID
    manifest_digest: str
    status: str
    submitted_at: datetime
    confirmed_at: datetime | None
    confirmation_valid_until: datetime | None
    idempotent_replay: bool = False


# ---------------------------------------------------------------- hub handover


class CreateHandoverManifestRequest(_Frozen):
    hub_id: UUID
    pickup_task_ids: list[UUID] = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=512)


class CancelHandoverManifestRequest(_Frozen):
    reason: str | None = Field(default=None, max_length=512)


class MediaRefRequest(_Frozen):
    ref_type: str = Field(min_length=1, max_length=64)
    bucket: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=512)
    content_type: str | None = Field(default=None, max_length=128)


class RecordHubReceiptRequest(_Frozen):
    shipment_id: UUID
    receiving_hub_id: UUID
    discrepancy_reason: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=512)
    media_refs: list[MediaRefRequest] = Field(default_factory=list, max_length=20)
    correlation_id: UUID | None = None


class CloseHandoverManifestRequest(_Frozen):
    missing_shipment_ids: list[UUID] = Field(default_factory=list, max_length=500)
    notes: str | None = Field(default=None, max_length=512)


class HandoverManifestItemResponse(_Frozen):
    item_id: UUID
    pickup_task_id: UUID
    shipment_id: UUID
    status: str
    added_at: datetime
    received_at: datetime | None
    received_hub_id: UUID | None
    discrepancy_reason: str | None
    releases_custody: bool


class HandoverManifestResponse(_Frozen):
    manifest_id: UUID
    manifest_code: str
    driver_user_id: str
    hub_id: UUID
    status: str
    expected_count: int
    received_count: int
    missing_count: int
    discrepancy_count: int
    created_at: datetime
    arrived_at_hub_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    items: list[HandoverManifestItemResponse]
    version: int
    idempotent_replay: bool = False


class HubReceiptResponse(_Frozen):
    manifest: HandoverManifestResponse
    item: HandoverManifestItemResponse
    custody_released: bool
    event_id: UUID | None
    idempotent_replay: bool = False


# -------------------------------------------------------------------- offline


class IssueOfflineAuthorizationRequest(_Frozen):
    device_id: str = Field(min_length=8, max_length=256)
    pickup_task_id: UUID
    requested_operations: list[str] = Field(default_factory=list, max_length=16)


class OfflineAuthorizationResponse(_Frozen):
    authorization_id: UUID
    resource_type: str
    resource_id: UUID
    assignment_revision: str
    permitted_operations: list[str]
    issued_at: datetime
    expires_at: datetime
    sync_deadline: datetime
    resource_snapshot: dict
    revoked_at: datetime | None = None
    revoked_reason: str | None = None


class IssuedOfflineAuthorizationResponse(_Frozen):
    """The only response that ever carries the offline token, and only once."""

    authorization: OfflineAuthorizationResponse
    authorization_token: str
    client_requirements: dict


class RevokeOfflineAuthorizationRequest(_Frozen):
    reason: str | None = Field(default=None, max_length=256)


class OfflineEventRequest(_Frozen):
    operation_id: UUID
    sequence: int = Field(ge=1)
    operation: str = Field(min_length=1, max_length=32)
    resource_id: UUID
    assignment_revision: str = Field(min_length=1, max_length=64)
    captured_at: datetime
    payload_fingerprint: str = Field(min_length=64, max_length=64)
    payload: dict = Field(default_factory=dict)


class SyncOfflineWorkRequest(_Frozen):
    device_id: str = Field(min_length=8, max_length=256)
    stream_id: UUID
    authorization_token: str = Field(min_length=16, max_length=4096)
    events: list[OfflineEventRequest] = Field(default_factory=list, max_length=500)


class SyncOutcomeResponse(_Frozen):
    event_id: UUID
    operation_id: UUID
    sequence: int
    status: str
    outcome_code: str
    detail: str
    replayed: bool


class SyncOfflineWorkResponse(_Frozen):
    stream_id: UUID
    authorization_id: UUID
    last_contiguous_sequence: int
    outcomes: list[SyncOutcomeResponse]
    counts: dict[str, int]


class ReconciliationCaseResponse(_Frozen):
    case_id: UUID
    offline_event_id: UUID
    driver_user_id: str
    resource_type: str
    resource_id: UUID
    status: str
    reason_code: str
    reason_detail: str
    authoritative_state: dict
    submitted_event: dict
    custody_implication: bool
    opened_at: datetime
    resolved_at: datetime | None
    resolved_by_user_id: str | None
    resolution: str | None
    resolution_notes: str | None


class ReconciliationCaseListResponse(_Frozen):
    cases: list[ReconciliationCaseResponse]


class ResolveReconciliationRequest(_Frozen):
    resolution: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=1024)
