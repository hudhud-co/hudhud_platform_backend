"""Request and response models for the Delivery HTTP adapter.

What is **not** here matters as much as what is.

* No response carries the delivery code. The code exists in the receiver's SMS and in
  the driver's memory for the length of one doorstep; it is never echoed back.
* No request or response carries an ID photograph (DRV-L07).
* The courier-facing rating response carries no rater and no note (SEC-08).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from delivery.domain.value_objects import (
    FailureReason,
    InspectionOutcome,
    IssueKind,
    IssueSource,
    NextAttemptDecision,
    PaymentMethod,
    PhotoStage,
    RatingTag,
    RefusalReason,
    SealCheckOutcome,
    StopStatus,
    VerificationMethod,
    VerificationOutcome,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoneyModel(_Model):
    """Exact IQD: integer minor units and an explicit currency. Never a float."""

    minor_units: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class MediaRefModel(_Model):
    bucket: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=512)
    content_type: str | None = Field(default=None, max_length=128)


class GeoPointModel(_Model):
    latitude: Decimal
    longitude: Decimal


class TimeWindowModel(_Model):
    starts_at_hour: int = Field(ge=0, le=23)
    ends_at_hour: int = Field(ge=1, le=24)


# ------------------------------------------------------------------ manifest


class OpenManifestRequest(_Model):
    hub_id: UUID


class ManifestResponse(_Model):
    manifest_id: UUID
    driver_principal_id: UUID
    hub_id: UUID
    created_at: datetime | None
    closed_at: datetime | None
    is_open: bool


class ParcelSettingsModel(_Model):
    open_box_allowed: bool = False
    photo_documentation: bool = False
    packaging_seal_code: str | None = Field(default=None, max_length=64)


#: The canonical shipment tracking code: `SHP-` a date, and six digits.
#:
#: Not invented here — it is fixed by the event payload schemas under
#: `contracts/events/`, including `delivery.fact.departed_for_receiver`,
#: `delivery.fact.delivered` and `delivery.fact.cod_collected`.
TRACKING_CODE_PATTERN = r"^SHP-\d{8}-\d{6}$"


class ScanParcelRequest(_Model):
    """The scan that transfers custody (v6.3 p.25).

    ``delivery_code`` is accepted, hashed against this stop, and discarded. It is never
    stored and never returned.
    """

    # Nine event payload schemas fix this shape, so a code that does not match
    # is refused where it enters rather than when the first fact is published —
    # which happened at `POST /depart`, as a 500 on an unrelated step, with the
    # parcel already in the driver's custody.
    tracking_code: str = Field(pattern=TRACKING_CODE_PATTERN)
    settings: ParcelSettingsModel = ParcelSettingsModel()
    delivery_code: str | None = Field(default=None, max_length=32)
    named_receiver: str | None = Field(default=None, max_length=160)
    cod_amount: MoneyModel | None = None
    payment_method_expected: PaymentMethod = PaymentMethod.PREPAID


class PaymentResponse(_Model):
    payment_id: UUID
    stop_id: UUID
    method: PaymentMethod
    outcome: str
    amount: MoneyModel | None
    pos_reference: str | None
    has_pos_receipt: bool
    enters_driver_cash_custody: bool
    recorded_at: datetime | None


class StopResponse(_Model):
    stop_id: UUID
    manifest_id: UUID
    tracking_code: str
    driver_principal_id: UUID
    status: StopStatus
    settings: ParcelSettingsModel
    #: Whether a code was set for this stop — never the code, and never the digest.
    has_delivery_code: bool
    named_receiver: str | None
    cod_amount: MoneyModel | None
    payment_method_expected: PaymentMethod
    requires_payment_at_door: bool
    requires_seal_check: bool
    custody_taken_at: datetime | None
    departed_at: datetime | None
    arrived_at: datetime | None
    wait_started_at: datetime | None
    wait_remaining_seconds: int | None = None
    verified_at: datetime | None
    verified_by_method: VerificationMethod | None
    seal_outcome: SealCheckOutcome | None
    inspection_outcome: InspectionOutcome | None
    delivered_at: datetime | None
    failure_reason: FailureReason | None
    refusal_reason: RefusalReason | None
    closed_at: datetime | None
    code_attempt_count: int
    #: What was already collected at this door, if anything.
    #:
    #: A driver whose app restarted between taking the cash and completing the
    #: delivery has no other way to know the money is already in their hand, and
    #: would ask the receiver for it a second time. The service refuses a second
    #: *recording*, so the ledger was never at risk — this is what stops the
    #: physical double collection.
    payment: PaymentResponse | None = None
    version: int


# ------------------------------------------------------------------ the door


class DepartRequest(_Model):
    """DRV-L01 — the receiver gets an ETA, never an exact time."""

    eta_from_minutes: int | None = Field(default=None, ge=0, le=1440)
    eta_to_minutes: int | None = Field(default=None, ge=0, le=1440)


class VerifyWithCodeRequest(_Model):
    code: str = Field(min_length=1, max_length=32)


class VerifyWithIdRequest(_Model):
    """The fallback for the named receiver only (v6.3 p.26).

    There is no field for a photograph of the document, and adding one is blocked by
    :class:`delivery.domain.id_evidence.IdEvidencePolicy` until retention is decided.
    """

    presented_name: str = Field(min_length=1, max_length=160)
    document_kind: str | None = Field(default=None, max_length=32)


class VerificationResponse(_Model):
    stop: StopResponse
    method: VerificationMethod
    outcome: VerificationOutcome
    attempts_used: int


class SealCheckRequest(_Model):
    outcome: SealCheckOutcome


class InspectionRequest(_Model):
    outcome: InspectionOutcome


class AttachPhotoRequest(_Model):
    stage: PhotoStage
    media: MediaRefModel


class PhotoResponse(_Model):
    photo_id: UUID
    stop_id: UUID
    stage: PhotoStage
    media: MediaRefModel
    captured_at: datetime


# ------------------------------------------------------------------ payment


class ConfirmPrepaidRequest(_Model):
    """v6.3 p.31 — prepaid means collect nothing at the door."""


class CollectCashRequest(_Model):
    """The amount is the COD on the parcel, not something the driver types.

    A driver-supplied figure could differ from what the merchant charged, so the amount
    comes from the stop and this request carries nothing.
    """


class CardApprovalRequest(_Model):
    """DRV-L14 — one of the two proofs is required; the amount comes from the parcel."""

    pos_reference: str | None = Field(default=None, max_length=64)
    pos_receipt: MediaRefModel | None = None


class CardDeclineRequest(_Model):
    """DRV-L13 — a decline is recorded and the driver falls back to cash."""


# ------------------------------------------------------------------ outcomes


class RefusalRequest(_Model):
    """Driver App v8 `lmRefuse` — "Reason is optional"."""

    reason: RefusalReason | None = None


class DeliveryResponse(_Model):
    stop: StopResponse
    payment: PaymentResponse | None


class FailedAttemptResponse(_Model):
    attempt_id: UUID
    stop_id: UUID
    tracking_code: str
    reason: FailureReason
    recorded_at: datetime
    next_attempt_decision: NextAttemptDecision | None
    decided_at: datetime | None
    awaits_operations: bool
    #: v6.3 p.29 — when the three-day hold runs out on this parcel.
    hold_expires_at: datetime


class FailureResponse(_Model):
    stop: StopResponse
    attempt: FailedAttemptResponse


class DecideNextAttemptRequest(_Model):
    """OPS-08 — the disposition is operations', never the driver's."""

    decision: NextAttemptDecision


# ------------------------------------------------------------------ receiver


class RaiseIncidentRequest(_Model):
    """DRV-L21 — a damage or loss incident opened by the driver from a stop."""

    kind: IssueKind
    detail: str | None = Field(default=None, max_length=2000)
    media: list[MediaRefModel] = Field(default_factory=list, max_length=10)


class ReturnedAfterHoldResponse(_Model):
    """DRV-L20 — what the three-day sweep changed (v6.3 p.29)."""

    hold_days: int
    returned: list[FailedAttemptResponse]


class HandoverPreferenceRequest(_Model):
    window: TimeWindowModel | None = None
    address_line: str | None = Field(default=None, max_length=256)
    landmark: str | None = Field(default=None, max_length=256)
    geo: GeoPointModel | None = None


class HandoverPreferenceResponse(_Model):
    preference_id: UUID
    tracking_code: str
    window: TimeWindowModel | None
    address_line: str | None
    landmark: str | None
    geo: GeoPointModel | None
    has_exact_location: bool
    updated_at: datetime | None


class ReportIssueRequest(_Model):
    kind: IssueKind
    detail: str | None = Field(default=None, max_length=2000)
    media: list[MediaRefModel] = Field(default_factory=list, max_length=10)


class IssueReportResponse(_Model):
    report_id: UUID
    tracking_code: str
    kind: IssueKind
    source: IssueSource
    stop_id: UUID | None
    detail: str | None
    media_count: int
    reported_at: datetime | None


class RateCourierRequest(_Model):
    score: int = Field(ge=1, le=5)
    tags: list[RatingTag] = Field(default_factory=list, max_length=7)
    note: str | None = Field(default=None, max_length=2000)


class RatingAcknowledgementResponse(_Model):
    """What the customer gets back. Their own note is theirs, so it is echoed."""

    rating_id: UUID
    tracking_code: str
    score: int
    tags: list[RatingTag]
    rated_at: datetime | None


class CourierRatingSummaryResponse(_Model):
    """SEC-08 — what a courier may see: no rater, no note, no per-parcel breakdown."""

    courier_principal_id: UUID
    rating_count: int
    average_score: float | None
    tag_counts: dict[str, int]


class ArrivalViewResponse(_Model):
    tracking_code: str
    status: StopStatus
    departed_at: datetime | None
    wait_minutes_at_door: int
    preference: HandoverPreferenceResponse | None


class DeliveryCodePolicyResponse(_Model):
    """DRV-L05 — the conflict, stated rather than resolved by a default."""

    decided: bool
    length: int | None
    conflicting_lengths: list[int]
    conflict_sources: dict[str, str]
