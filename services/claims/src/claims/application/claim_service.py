"""Filing, reviewing and deciding a compensation claim (CLM-01 … CLM-07).

The sequence v6.3 lays out is deliberate and this service keeps it: a claim is filed
(p.42), the scan and custody records are read (p.42), and only then is it approved or
rejected with a documented reason (p.43). The state machine makes review a step that
cannot be skipped, because "we reviewed the records" is the whole basis on which HUDHUD
either pays or declines to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from claims.domain.entities import (
    ClaimMessage,
    ClaimSummaryForClaimant,
    ClaimSummaryForDriver,
    CompensationClaim,
)
from claims.domain.errors import (
    ClaimAlreadyDecided,
    ClaimAlreadyOpenForThisParcel,
    ClaimNotFound,
    ClaimTransitionNotAllowed,
    ClosedClaimTakesNoMessages,
    CompensationAmountRequired,
    CustodyRecordsNotReviewed,
    InvalidTrackingCode,
    LiabilityEndedAtTheDoor,
    MessageBodyRequired,
    NoReturnWindowAfterAcceptance,
    NotYourClaim,
    OnlyOperationsDecidesAClaim,
    OnlySupportOrOperationsReviews,
    PhotographsRequired,
    RejectionReasonRequired,
)
from claims.domain.money import Money
from claims.domain.value_objects import (
    HUDHUD_STILL_LIABLE,
    ClaimKind,
    ClaimOpenedBy,
    ClaimStatus,
    ConversationAuthor,
    CustodyBoundary,
    EvidenceMediaRef,
    RejectionReason,
    build_claim_reference,
    is_valid_tracking_code,
)
from claims.ports.repository import ClaimsUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class FiledClaim:
    claim: CompensationClaim
    reference: str


class ClaimService:
    def __init__(self, unit_of_work: ClaimsUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------------------- CLM-02, 07

    def file_claim(
        self,
        *,
        tracking_code: str,
        kind: ClaimKind,
        opened_by: ClaimOpenedBy,
        opened_by_principal_id: UUID | None = None,
        sender_principal_id: UUID | None = None,
        merchant_id: UUID | None = None,
        description: str | None = None,
        evidence: tuple[EvidenceMediaRef, ...] = (),
        custody_boundary: CustodyBoundary = CustodyBoundary.IN_HUDHUD_CUSTODY,
    ) -> FiledClaim:
        """v6.3 p.42 — the sender, the receiver **or the driver** may open a claim.

        Two things are refused at the boundary rather than accepted and then rejected.
        A damage claim with no photograph cannot be checked against the custody record
        (Customer App v3 `photosRequired`), and a parcel the receiver took inside to test
        is no longer HUDHUD's (p.37) — telling someone that now is kinder and cheaper
        than telling them after a review.
        """
        if not is_valid_tracking_code(tracking_code):
            raise InvalidTrackingCode(tracking_code)
        if kind in {ClaimKind.DAMAGED_IN_TRANSIT} and not evidence:
            raise PhotographsRequired()
        if custody_boundary not in HUDHUD_STILL_LIABLE:
            # Filing and approval have to agree about where liability ended, or a claim
            # can be accepted and then refused for a reason that was knowable on day
            # one. v6.3 p.37 names the open-box case explicitly; a completed sealed
            # handover is treated the same way, because CLM-01's responsibility runs
            # "while in its custody" and by then it is not.
            raise LiabilityEndedAtTheDoor(custody_boundary.value)

        self._uow.begin()
        try:
            existing = self._uow.claims.find_open_for_parcel(tracking_code)
            if existing is not None:
                raise ClaimAlreadyOpenForThisParcel(existing.reference)

            moment = _now()
            day = moment.strftime("%Y%m%d")
            reference = build_claim_reference(
                day=day, sequence=self._uow.claims.next_sequence_for_day(day)
            )
            claim = CompensationClaim(
                claim_id=uuid4(),
                reference=reference,
                tracking_code=tracking_code,
                kind=kind,
                opened_by=opened_by,
                opened_by_principal_id=opened_by_principal_id,
                # CLM-01 — whoever opened it, the sender is who gets compensated.
                sender_principal_id=sender_principal_id,
                merchant_id=merchant_id,
                description=description,
                evidence=evidence,
                custody_boundary=custody_boundary,
                status=ClaimStatus.SUBMITTED,
                submitted_at=moment,
            )
            self._uow.claims.save(claim)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return FiledClaim(claim=claim, reference=reference)

    def withdraw(
        self, *, reference: str, principal_id: UUID
    ) -> CompensationClaim:
        """A claimant may withdraw their own claim while it is still open."""
        self._uow.begin()
        try:
            claim = self._require(reference)
            self._assert_is_the_claimant(claim, principal_id)
            if not claim.can_transition_to(ClaimStatus.WITHDRAWN):
                raise ClaimTransitionNotAllowed(
                    claim.status.value, ClaimStatus.WITHDRAWN.value
                )
            claim.status = ClaimStatus.WITHDRAWN
            claim.withdrawn_at = _now()
            claim.version += 1
            self._uow.claims.save(claim)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return claim

    # ------------------------------------------------------------- CLM-06

    def request_return(self, *, tracking_code: str) -> None:
        """CLM-06 — v6.3: there is no return window after acceptance at the door.

        Always refuses, and exists so that the absence is a stated decision rather than
        a missing endpoint somebody later "fixes". A receiver who accepted the parcel
        cannot change their mind afterwards; what they *can* still do is file a claim if
        the parcel was damaged or short, which is a different question and is not
        blocked here.
        """
        _ = tracking_code
        raise NoReturnWindowAfterAcceptance()

    # ------------------------------------------------------------- CLM-03

    def start_review(
        self, *, reference: str, actor, custody_records_reviewed: bool = False
    ) -> CompensationClaim:
        """v6.3 p.42 — "Hudhud reviews scan and custody records before compensating".

        ``custody_records_reviewed`` is recorded rather than assumed, and
        :meth:`approve` refuses without it. The flag is the reviewer's assertion that
        they looked; making it a separate, explicit act is what stops "reviewed" from
        meaning "the ticket passed through this status".
        """
        if not actor.may_review_a_claim:
            raise OnlySupportOrOperationsReviews()
        self._uow.begin()
        try:
            claim = self._require(reference)
            if not claim.can_transition_to(ClaimStatus.UNDER_REVIEW):
                raise ClaimTransitionNotAllowed(
                    claim.status.value, ClaimStatus.UNDER_REVIEW.value
                )
            claim.status = ClaimStatus.UNDER_REVIEW
            claim.review_started_at = _now()
            claim.reviewed_by_actor_id = actor.principal_id
            claim.custody_records_reviewed = custody_records_reviewed
            claim.version += 1
            self._uow.claims.save(claim)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return claim

    def record_custody_review(
        self, *, reference: str, actor
    ) -> CompensationClaim:
        """Mark that the scan and custody records have actually been read."""
        if not actor.may_review_a_claim:
            raise OnlySupportOrOperationsReviews()
        self._uow.begin()
        try:
            claim = self._require(reference)
            if claim.is_decided:
                raise ClaimAlreadyDecided(claim.status.value)
            claim.custody_records_reviewed = True
            claim.reviewed_by_actor_id = actor.principal_id
            claim.version += 1
            self._uow.claims.save(claim)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return claim

    # ------------------------------------------------------------- CLM-04

    def approve(
        self, *, reference: str, actor, compensation: Money
    ) -> CompensationClaim:
        """v6.3 p.43 — "approved ⇒ sender compensated" (CLM-01).

        Deciding money is narrower than reviewing: support may review and talk to the
        claimant, but committing HUDHUD to a payment is Operations' or an accountant's.
        """
        if not actor.may_decide_a_claim:
            raise OnlyOperationsDecidesAClaim()
        if compensation is None or compensation.is_zero:
            raise CompensationAmountRequired()

        self._uow.begin()
        try:
            claim = self._require(reference)
            if not claim.can_transition_to(ClaimStatus.APPROVED):
                raise ClaimTransitionNotAllowed(
                    claim.status.value, ClaimStatus.APPROVED.value
                )
            if not claim.custody_records_reviewed:
                raise CustodyRecordsNotReviewed()
            if not claim.hudhud_is_liable:
                # Belt and braces: filing already refuses this, and a boundary that
                # changed during the review must not be able to slip past.
                raise LiabilityEndedAtTheDoor(claim.custody_boundary.value)

            claim.status = ClaimStatus.APPROVED
            claim.compensation_amount = compensation
            claim.decided_at = _now()
            claim.decided_by_actor_id = actor.principal_id
            claim.version += 1
            self._uow.claims.save(claim)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return claim

    def reject(
        self,
        *,
        reference: str,
        actor,
        reason: RejectionReason,
        note: str | None = None,
    ) -> CompensationClaim:
        """v6.3 p.43 — "rejected ⇒ documented reason given".

        The reason is a closed enum rather than free text so that a rejection can be
        counted and audited; the note carries the specifics.
        """
        if not actor.may_decide_a_claim:
            raise OnlyOperationsDecidesAClaim()
        if reason is None:
            raise RejectionReasonRequired()
        if reason is RejectionReason.OTHER and not (note or "").strip():
            raise RejectionReasonRequired()

        self._uow.begin()
        try:
            claim = self._require(reference)
            if not claim.can_transition_to(ClaimStatus.REJECTED):
                raise ClaimTransitionNotAllowed(
                    claim.status.value, ClaimStatus.REJECTED.value
                )
            if not claim.custody_records_reviewed:
                raise CustodyRecordsNotReviewed()
            claim.status = ClaimStatus.REJECTED
            claim.rejection_reason = reason
            claim.rejection_note = (note or "").strip() or None
            claim.decided_at = _now()
            claim.decided_by_actor_id = actor.principal_id
            claim.version += 1
            self._uow.claims.save(claim)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return claim

    # ------------------------------------------------------------- CLM-07

    def post_message(
        self,
        *,
        reference: str,
        author: ConversationAuthor,
        body: str,
        author_principal_id: UUID | None = None,
        attachments: tuple[EvidenceMediaRef, ...] = (),
    ) -> ClaimMessage:
        """The support conversation that goes with a claim."""
        if not body.strip():
            raise MessageBodyRequired()
        self._uow.begin()
        try:
            claim = self._require(reference)
            if not claim.is_open:
                raise ClosedClaimTakesNoMessages()
            message = ClaimMessage(
                message_id=uuid4(),
                claim_id=claim.claim_id,
                author=author,
                body=body.strip(),
                written_at=_now(),
                author_principal_id=author_principal_id,
                attachments=attachments,
            )
            self._uow.messages.save(message)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return message

    def conversation(
        self, *, reference: str, principal_id: UUID | None = None, actor=None
    ) -> tuple[ClaimMessage, ...]:
        """The thread. A claimant sees their own; support and operations see any."""
        self._uow.begin()
        try:
            claim = self._require(reference)
            if actor is None or not actor.may_review_a_claim:
                self._assert_is_the_claimant(claim, principal_id)
            found = self._uow.messages.list_for_claim(claim.claim_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- reads

    def for_claimant(
        self, *, reference: str, principal_id: UUID
    ) -> ClaimSummaryForClaimant:
        """CLM-07 — what the person who filed sees about their own claim."""
        self._uow.begin()
        try:
            claim = self._require(reference)
            self._assert_is_the_claimant(claim, principal_id)
            messages = self._uow.messages.count_for_claim(claim.claim_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return ClaimSummaryForClaimant(
            reference=claim.reference,
            tracking_code=claim.tracking_code,
            kind=claim.kind,
            status=claim.status,
            submitted_at=claim.submitted_at,
            decided_at=claim.decided_at,
            # Only once approved: before that there is no amount, and showing a proposed
            # one would imply a decision nobody has made.
            compensation_amount=(
                claim.compensation_amount
                if claim.status is ClaimStatus.APPROVED
                else None
            ),
            rejection_reason=claim.rejection_reason,
            rejection_note=claim.rejection_note,
            message_count=messages,
        )

    def for_driver(self, *, reference: str) -> ClaimSummaryForDriver:
        """SEC-07 — what a driver may see. There is no amount, and no field for one."""
        self._uow.begin()
        try:
            claim = self._require(reference)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return ClaimSummaryForDriver(
            reference=claim.reference,
            kind=claim.kind.value,
            status=claim.status.value,
            tracking_code=claim.tracking_code,
            submitted_at=claim.submitted_at,
            resolved_at=claim.decided_at,
        )

    def my_claims(self, *, principal_id: UUID) -> tuple[CompensationClaim, ...]:
        self._uow.begin()
        try:
            found = self._uow.claims.list_for_principal(principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def awaiting_operations(self) -> tuple[CompensationClaim, ...]:
        self._uow.begin()
        try:
            found = self._uow.claims.list_awaiting_operations()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def claim(self, *, reference: str) -> CompensationClaim:
        self._uow.begin()
        try:
            found = self._require(reference)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _require(self, reference: str) -> CompensationClaim:
        claim = self._uow.claims.find_by_reference(reference)
        if claim is None:
            raise ClaimNotFound(reference)
        return claim

    @staticmethod
    def _assert_is_the_claimant(
        claim: CompensationClaim, principal_id: UUID | None
    ) -> None:
        """Their own claim: the one they opened, or the one that compensates them."""
        if principal_id is None:
            raise NotYourClaim()
        if principal_id in {
            claim.opened_by_principal_id,
            claim.sender_principal_id,
        }:
            return
        raise NotYourClaim()
