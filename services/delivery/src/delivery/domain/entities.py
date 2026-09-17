"""Delivery aggregates: manifest, stop, verification, inspection, payment, outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from delivery.domain.money import Money
from delivery.domain.value_objects import (
    DOOR_WAIT_SECONDS,
    HOLD_DAYS,
    IN_DRIVER_CUSTODY,
    STOP_TRANSITIONS,
    EvidenceMediaRef,
    FailureReason,
    GeoPoint,
    InspectionOutcome,
    IssueKind,
    IssueSource,
    NextAttemptDecision,
    PaymentMethod,
    PaymentOutcome,
    PhotoStage,
    RatingTag,
    RefusalReason,
    SealCheckOutcome,
    StopStatus,
    TimeWindow,
    VerificationMethod,
    VerificationOutcome,
)


@dataclass(slots=True)
class DeliveryManifest:
    """A last-mile driver's round, built by scanning at the destination hub.

    v6.3 p.25: the driver "builds their manifest by scanning assigned parcels", and that
    scan is what transfers custody. The manifest is therefore not a plan — it is the
    record of what the driver is carrying.
    """

    manifest_id: UUID
    driver_principal_id: UUID
    hub_id: UUID
    created_at: datetime | None = None
    closed_at: datetime | None = None
    version: int = 1

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


@dataclass(slots=True)
class ParcelSettings:
    """What the merchant decided, carried to the door (MER-10, MER-11, MER-12).

    All three default off, matching v6.3 p.13 and p.14. ``photo_documentation`` off means
    no photo is taken at any stage, and ``open_box_allowed`` off means the receiver
    accepts or refuses the parcel sealed.
    """

    open_box_allowed: bool = False
    photo_documentation: bool = False
    packaging_seal_code: str | None = None

    @property
    def has_parcel_seal(self) -> bool:
        return self.packaging_seal_code is not None


@dataclass(slots=True)
class VerificationAttempt:
    """One attempt to prove the person at the door may take the parcel.

    There is deliberately no field for the code that was typed, and none for an ID
    photograph. What is kept is the method, the outcome, the actor and the time — the
    part both product sources agree belongs on the record.
    """

    attempt_id: UUID
    stop_id: UUID
    method: VerificationMethod
    outcome: VerificationOutcome
    attempted_at: datetime
    attempted_by_actor_id: UUID
    #: Only meaningful for the ID fallback: whether the name matched the named receiver.
    id_matched_named_receiver: bool | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is VerificationOutcome.VERIFIED


@dataclass(slots=True)
class PaymentRecord:
    """What was collected at the door, and what proves it (v6.3 p.31).

    A POS payment must carry proof before the parcel changes hands — Driver App v8
    `lmPayApproved` labels it "PROOF OF PAYMENT — ONE IS REQUIRED" and disables completion
    without a transaction number or a receipt photo.
    """

    payment_id: UUID
    stop_id: UUID
    method: PaymentMethod
    outcome: PaymentOutcome
    amount: Money | None = None
    pos_reference: str | None = None
    pos_receipt: EvidenceMediaRef | None = None
    recorded_at: datetime | None = None
    recorded_by_actor_id: UUID | None = None

    @property
    def has_pos_proof(self) -> bool:
        return bool(self.pos_reference or self.pos_receipt)

    @property
    def enters_driver_cash_custody(self) -> bool:
        """Only cash does.

        Driver App v8 `lmPayApproved`: "Card payments go straight to HUDHUD — this amount
        is not added to your cash on hand." Counting a card payment into custody would
        make a driver liable for money they never held.
        """
        return (
            self.method is PaymentMethod.CASH
            and self.outcome is PaymentOutcome.COLLECTED
        )


@dataclass(slots=True)
class PhotoEvidence:
    """A delivery photo, stored as an opaque reference (MER-11)."""

    photo_id: UUID
    stop_id: UUID
    stage: PhotoStage
    media: EvidenceMediaRef
    captured_at: datetime
    captured_by_actor_id: UUID


@dataclass(slots=True)
class DeliveryStop:
    """One parcel at one door.

    The status machine is the v6.3 delivery chapter in order: arrive, wait if nobody
    answers, verify, check the seal, inspect if open-box is on, take payment, hand over.
    Custody sits with the driver at every status except ``DELIVERED``.
    """

    stop_id: UUID
    manifest_id: UUID
    tracking_code: str
    driver_principal_id: UUID
    status: StopStatus = StopStatus.ASSIGNED
    settings: ParcelSettings = field(default_factory=ParcelSettings)
    #: Keyed digest, bound to this stop. The code itself is never stored.
    delivery_code_digest: str | None = None
    named_receiver: str | None = None
    cod_amount: Money | None = None
    payment_method_expected: PaymentMethod = PaymentMethod.PREPAID

    custody_taken_at: datetime | None = None
    departed_at: datetime | None = None
    arrived_at: datetime | None = None
    wait_started_at: datetime | None = None
    verified_at: datetime | None = None
    verified_by_method: VerificationMethod | None = None
    seal_outcome: SealCheckOutcome | None = None
    inspection_outcome: InspectionOutcome | None = None
    delivered_at: datetime | None = None
    failure_reason: FailureReason | None = None
    refusal_reason: RefusalReason | None = None
    closed_at: datetime | None = None
    code_attempt_count: int = 0
    version: int = 1

    # ------------------------------------------------------------- state

    def can_transition_to(self, target: StopStatus) -> bool:
        return target in STOP_TRANSITIONS[self.status]

    @property
    def is_terminal(self) -> bool:
        return not STOP_TRANSITIONS[self.status]

    @property
    def in_driver_custody(self) -> bool:
        return self.status in IN_DRIVER_CUSTODY

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    # ------------------------------------------------------------- the door

    def wait_elapsed_seconds(self, moment: datetime) -> int:
        if self.wait_started_at is None:
            return 0
        return max(0, int((moment - self.wait_started_at).total_seconds()))

    def wait_is_over(self, moment: datetime) -> bool:
        """v6.3 p.26 — a failed attempt is recordable only after the ten minutes."""
        return self.wait_elapsed_seconds(moment) >= DOOR_WAIT_SECONDS

    def wait_ends_at(self) -> datetime | None:
        if self.wait_started_at is None:
            return None
        return self.wait_started_at + timedelta(seconds=DOOR_WAIT_SECONDS)

    @property
    def requires_payment_at_door(self) -> bool:
        """v6.3 p.31 — a COD parcel is not Delivered unless payment was collected."""
        return self.payment_method_expected is not PaymentMethod.PREPAID

    @property
    def requires_seal_check(self) -> bool:
        return self.settings.has_parcel_seal

    @property
    def requires_photo_before_opening(self) -> bool:
        """Only when both add-ons are on: photo documentation *and* open-box (p.14)."""
        return self.settings.photo_documentation and self.settings.open_box_allowed

    @property
    def requires_photo_after_inspection(self) -> bool:
        return self.settings.photo_documentation


@dataclass(slots=True)
class FailedAttempt:
    """A door visit that ended without a handover (v6.3 p.28, p.29).

    What happens next is not the driver's call: Driver App v8 `lmFailed` says the parcel
    is "Held for next attempt — decided by operations" (OPS-08).
    """

    attempt_id: UUID
    stop_id: UUID
    tracking_code: str
    reason: FailureReason
    recorded_at: datetime
    recorded_by_actor_id: UUID
    next_attempt_decision: NextAttemptDecision | None = None
    decided_at: datetime | None = None
    decided_by_actor_id: UUID | None = None
    version: int = 1

    @property
    def awaits_operations(self) -> bool:
        return self.next_attempt_decision is None

    def hold_expires_at(self) -> datetime:
        """v6.3 p.29 — three days from the failed attempt, then back to the merchant."""
        return self.recorded_at + timedelta(days=HOLD_DAYS)

    def hold_is_over(self, moment: datetime) -> bool:
        return moment >= self.hold_expires_at()

    def must_return_to_the_merchant(self, moment: datetime) -> bool:
        """Still held once the three days are up.

        A parcel already decided for retry or return is out of scope: this is only the
        one nobody moved on, which is exactly what the hold was introduced to bound.
        """
        if self.next_attempt_decision is NextAttemptDecision.RETURN_TO_MERCHANT:
            return False
        if self.next_attempt_decision is NextAttemptDecision.RETRY:
            return False
        return self.hold_is_over(moment)


@dataclass(slots=True)
class ReceiverPreference:
    """What the receiver asked for, set from the tracking link or the app (CUS-11).

    v6.3 p.20 is explicit that this is why the WhatsApp message and the tracking site
    encourage installing the app: a time window and an exact location mean fewer failed
    attempts.
    """

    preference_id: UUID
    tracking_code: str
    window: TimeWindow | None = None
    address_line: str | None = None
    landmark: str | None = None
    geo: GeoPoint | None = None
    set_by_principal_id: UUID | None = None
    updated_at: datetime | None = None
    version: int = 1

    @property
    def has_exact_location(self) -> bool:
        return self.geo is not None


@dataclass(slots=True)
class ParcelIssueReport:
    """A problem reported against a parcel (CUS-12 for the receiver, DRV-L21 for the driver).

    Delivery records the report and nothing more. Whether it becomes a claim, and what
    it is worth, is the Claims context's decision (ADR-0012).
    """

    report_id: UUID
    tracking_code: str
    kind: IssueKind
    source: IssueSource = IssueSource.RECEIVER
    detail: str | None = None
    reported_by_principal_id: UUID | None = None
    #: Only a driver report is tied to a stop: a receiver may report after delivery.
    stop_id: UUID | None = None
    media: tuple[EvidenceMediaRef, ...] = ()
    reported_at: datetime | None = None
    version: int = 1


@dataclass(slots=True)
class CourierRating:
    """A private rating of a courier (CUS-13, SEC-08).

    Customer App v3 `rateCourier`: "Your rating stays private" and "The courier never sees
    your name or your note." The note and the rater are held here, and the read model the
    courier sees carries neither.
    """

    rating_id: UUID
    courier_principal_id: UUID
    tracking_code: str
    score: int
    tags: tuple[RatingTag, ...] = ()
    note: str | None = None
    rated_by_principal_id: UUID | None = None
    rated_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.score <= 5:
            msg = "a courier rating is between 1 and 5"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CourierRatingSummary:
    """What a courier may see about their own ratings.

    Deliberately an aggregate with no rater and no note: showing either would break the
    promise the customer was given when they rated.
    """

    courier_principal_id: UUID
    rating_count: int
    average_score: float | None
    tag_counts: dict[str, int] = field(default_factory=dict)
