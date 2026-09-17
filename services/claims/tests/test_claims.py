"""Filing, reviewing and deciding a compensation claim (CLM-01 … CLM-07).

The v6.3 rules these hold to are all Confirmed decisions, several of them reversals from
v5, so each test names the page it comes from.
"""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest
from claims_fixtures import (
    accountant,
    build_lab,
    customer,
    driver,
    iqd,
    next_tracking_code,
    operations,
    photo,
    support,
)

from claims.domain.entities import ClaimSummaryForClaimant, ClaimSummaryForDriver
from claims.domain.errors import (
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
from claims.domain.value_objects import (
    ClaimKind,
    ClaimOpenedBy,
    ClaimStatus,
    ConversationAuthor,
    CustodyBoundary,
    RejectionReason,
    is_valid_claim_reference,
)
from claims.ports.authorization import ClaimsActor, ClaimsRole


def file_damage(lab, **overrides):
    defaults = {
        "tracking_code": next_tracking_code(),
        "kind": ClaimKind.DAMAGED_IN_TRANSIT,
        "opened_by": ClaimOpenedBy.SENDER,
        "opened_by_principal_id": lab.sender_id,
        "sender_principal_id": lab.sender_id,
        "evidence": (photo(),),
    }
    defaults.update(overrides)
    return lab.claims.file_claim(**defaults)


def reviewed(lab, reference, actor=None):
    lab.claims.start_review(
        reference=reference, actor=actor or support(), custody_records_reviewed=True
    )


# ------------------------------------------------------------------ CLM-02


def test_a_claim_may_be_opened_by_the_sender_the_receiver_or_the_driver() -> None:
    """v6.3 p.42."""
    for opened_by, principal in (
        (ClaimOpenedBy.SENDER, "sender_id"),
        (ClaimOpenedBy.RECEIVER, "receiver_id"),
        (ClaimOpenedBy.DRIVER, "driver_id"),
    ):
        lab = build_lab()
        filed = file_damage(
            lab,
            opened_by=opened_by,
            opened_by_principal_id=getattr(lab, principal),
            sender_principal_id=lab.sender_id,
        )
        assert filed.claim.opened_by is opened_by


def test_the_reference_has_the_shape_the_customer_app_shows() -> None:
    """Customer App v3 `claimSubmitted`: "Claim submitted · CLM-20260802-000003"."""
    lab = build_lab()
    filed = file_damage(lab)
    assert is_valid_claim_reference(filed.reference), filed.reference


def test_references_do_not_repeat_within_a_day() -> None:
    lab = build_lab()
    references = {file_damage(lab).reference for _ in range(5)}
    assert len(references) == 5


# ------------------------------------------------------------------ CLM-01


def test_the_sender_is_compensated_whoever_opened_the_claim() -> None:
    """v6.3 p.40, a Confirmed decision reversed from v5.

    A receiver may open the claim; the money still goes to the person who paid HUDHUD to
    carry the parcel. Conflating the two is how a receiver gets compensated for a parcel
    they never paid for.
    """
    lab = build_lab()
    filed = file_damage(
        lab,
        opened_by=ClaimOpenedBy.RECEIVER,
        opened_by_principal_id=lab.receiver_id,
        sender_principal_id=lab.sender_id,
    )
    assert filed.claim.opened_by_principal_id == lab.receiver_id
    assert filed.claim.compensated_principal_id == lab.sender_id


# ------------------------------------------------------------------ CLM-05


def test_liability_stays_with_hudhud_during_an_open_box_check_at_the_door() -> None:
    """v6.3 p.37 — the courier is standing there; it is still HUDHUD's."""
    lab = build_lab()
    filed = file_damage(
        lab, custody_boundary=CustodyBoundary.OPEN_BOX_AT_THE_DOOR
    )
    assert filed.claim.hudhud_is_liable is True


def test_liability_ends_when_the_receiver_takes_it_inside_to_test_it() -> None:
    """v6.3 p.37, and Customer App v3 `openBoxBody` says the same to the receiver."""
    lab = build_lab()
    with pytest.raises(LiabilityEndedAtTheDoor):
        file_damage(lab, custody_boundary=CustodyBoundary.TAKEN_INSIDE_TO_TEST)


def test_a_parcel_still_in_custody_is_plainly_hudhuds() -> None:
    lab = build_lab()
    filed = file_damage(lab, custody_boundary=CustodyBoundary.IN_HUDHUD_CUSTODY)
    assert filed.claim.hudhud_is_liable is True


# ------------------------------------------------------------------ filing rules


def test_a_damage_claim_needs_a_photograph() -> None:
    """Customer App v3 `photosRequired`, shown under "Damaged in transit"."""
    lab = build_lab()
    with pytest.raises(PhotographsRequired):
        file_damage(lab, evidence=())


def test_a_lost_parcel_claim_needs_no_photograph() -> None:
    """There is nothing to photograph; requiring one would block a real claim."""
    lab = build_lab()
    filed = file_damage(lab, kind=ClaimKind.LOST_PARCEL, evidence=())
    assert filed.claim.kind is ClaimKind.LOST_PARCEL


def test_a_wrong_cod_amount_claim_needs_no_photograph() -> None:
    lab = build_lab()
    filed = file_damage(lab, kind=ClaimKind.WRONG_COD_AMOUNT, evidence=())
    assert filed.claim.status is ClaimStatus.SUBMITTED


def test_the_three_kinds_are_the_ones_the_customer_app_offers() -> None:
    """`fileAClaimSub`: "damaged, lost, or delivered with the wrong amount collected"."""
    assert {kind.value for kind in ClaimKind} == {
        "DAMAGED_IN_TRANSIT",
        "LOST_PARCEL",
        "WRONG_COD_AMOUNT",
    }


def test_a_parcel_has_one_open_claim_at_a_time() -> None:
    lab = build_lab()
    code = next_tracking_code()
    file_damage(lab, tracking_code=code)
    with pytest.raises(ClaimAlreadyOpenForThisParcel):
        file_damage(lab, tracking_code=code)


def test_a_malformed_tracking_code_is_refused() -> None:
    lab = build_lab()
    with pytest.raises(InvalidTrackingCode):
        file_damage(lab, tracking_code="not-a-parcel")


# ------------------------------------------------------------------ CLM-03


def test_a_claim_cannot_be_approved_without_reading_the_custody_record() -> None:
    """v6.3 p.42 — "Hudhud reviews scan and custody records before compensating"."""
    lab = build_lab()
    filed = file_damage(lab)
    lab.claims.start_review(
        reference=filed.reference, actor=support(), custody_records_reviewed=False
    )
    with pytest.raises(CustodyRecordsNotReviewed):
        lab.claims.approve(
            reference=filed.reference, actor=operations(), compensation=iqd(50_000)
        )


def test_a_claim_cannot_be_rejected_without_reading_it_either() -> None:
    """A rejection is a decision about the records too, not a way to skip them."""
    lab = build_lab()
    filed = file_damage(lab)
    lab.claims.start_review(
        reference=filed.reference, actor=support(), custody_records_reviewed=False
    )
    with pytest.raises(CustodyRecordsNotReviewed):
        lab.claims.reject(
            reference=filed.reference,
            actor=operations(),
            reason=RejectionReason.NO_EVIDENCE_PROVIDED,
        )


def test_a_decision_cannot_skip_review_entirely() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    with pytest.raises(ClaimTransitionNotAllowed):
        lab.claims.approve(
            reference=filed.reference, actor=operations(), compensation=iqd(10_000)
        )


def test_the_review_records_who_read_it() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewer = support()
    lab.claims.start_review(
        reference=filed.reference, actor=reviewer, custody_records_reviewed=True
    )
    claim = lab.claims.claim(reference=filed.reference)
    assert claim.reviewed_by_actor_id == reviewer.principal_id
    assert claim.custody_records_reviewed is True


# ------------------------------------------------------------------ CLM-04


def test_an_approved_claim_carries_what_is_being_paid() -> None:
    """v6.3 p.43 — "approved ⇒ sender compensated"."""
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    approved = lab.claims.approve(
        reference=filed.reference, actor=operations(), compensation=iqd(75_000)
    )
    assert approved.status is ClaimStatus.APPROVED
    assert approved.compensation_amount == iqd(75_000)


def test_an_approval_with_no_amount_is_refused() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    with pytest.raises(CompensationAmountRequired):
        lab.claims.approve(
            reference=filed.reference, actor=operations(), compensation=iqd(0)
        )


def test_a_rejected_claim_carries_a_documented_reason() -> None:
    """v6.3 p.43 — "rejected ⇒ documented reason given"."""
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    rejected = lab.claims.reject(
        reference=filed.reference,
        actor=operations(),
        reason=RejectionReason.CUSTODY_RECORD_DOES_NOT_SUPPORT_IT,
        note="every scan shows the parcel intact through to handover",
    )
    assert rejected.status is ClaimStatus.REJECTED
    assert rejected.rejection_reason is RejectionReason.CUSTODY_RECORD_DOES_NOT_SUPPORT_IT
    assert rejected.rejection_note


def test_a_rejection_of_other_must_say_what_other_means() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    with pytest.raises(RejectionReasonRequired):
        lab.claims.reject(
            reference=filed.reference, actor=operations(), reason=RejectionReason.OTHER
        )


def test_a_decided_claim_is_decided_once() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    lab.claims.approve(
        reference=filed.reference, actor=operations(), compensation=iqd(10_000)
    )
    with pytest.raises(ClaimTransitionNotAllowed):
        lab.claims.reject(
            reference=filed.reference,
            actor=operations(),
            reason=RejectionReason.NO_EVIDENCE_PROVIDED,
        )


# ------------------------------------------- who may do what (not widened)


def test_support_may_review_but_may_not_decide_the_money() -> None:
    """Deliberately narrower than review: paying is Operations' or an accountant's."""
    lab = build_lab()
    filed = file_damage(lab)
    lab.claims.start_review(
        reference=filed.reference, actor=support(), custody_records_reviewed=True
    )
    with pytest.raises(OnlyOperationsDecidesAClaim):
        lab.claims.approve(
            reference=filed.reference, actor=support(), compensation=iqd(10_000)
        )


def test_an_accountant_may_decide() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    approved = lab.claims.approve(
        reference=filed.reference, actor=accountant(), compensation=iqd(20_000)
    )
    assert approved.status is ClaimStatus.APPROVED


def test_a_customer_may_not_review_or_decide_their_own_claim() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    with pytest.raises(OnlySupportOrOperationsReviews):
        lab.claims.start_review(
            reference=filed.reference, actor=customer(lab.sender_id)
        )


def test_a_driver_may_not_decide_a_claim() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    with pytest.raises(OnlyOperationsDecidesAClaim):
        lab.claims.approve(
            reference=filed.reference,
            actor=driver(lab.driver_id),
            compensation=iqd(10_000),
        )


# ------------------------------------------------------------------ CLM-07


def test_the_claimant_reads_their_own_claim() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    view = lab.claims.for_claimant(
        reference=filed.reference, principal_id=lab.sender_id
    )
    assert isinstance(view, ClaimSummaryForClaimant)
    assert view.reference == filed.reference


def test_a_stranger_cannot_read_someone_elses_claim() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    with pytest.raises(NotYourClaim):
        lab.claims.for_claimant(reference=filed.reference, principal_id=uuid4())


def test_a_receiver_who_opened_it_can_read_it_too() -> None:
    lab = build_lab()
    filed = file_damage(
        lab,
        opened_by=ClaimOpenedBy.RECEIVER,
        opened_by_principal_id=lab.receiver_id,
        sender_principal_id=lab.sender_id,
    )
    assert lab.claims.for_claimant(
        reference=filed.reference, principal_id=lab.receiver_id
    ).reference == filed.reference


def test_the_claimant_sees_no_amount_before_it_is_approved() -> None:
    """There is no amount yet; showing one would imply a decision nobody has made."""
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    view = lab.claims.for_claimant(
        reference=filed.reference, principal_id=lab.sender_id
    )
    assert view.compensation_amount is None


def test_the_claimant_sees_the_amount_once_it_is_approved() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    lab.claims.approve(
        reference=filed.reference, actor=operations(), compensation=iqd(64_000)
    )
    view = lab.claims.for_claimant(
        reference=filed.reference, principal_id=lab.sender_id
    )
    assert view.compensation_amount == iqd(64_000)


def test_the_claimant_sees_why_it_was_rejected() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    lab.claims.reject(
        reference=filed.reference,
        actor=operations(),
        reason=RejectionReason.DAMAGE_PRESENT_BEFORE_PICKUP,
        note="the pickup condition note records the same dent",
    )
    view = lab.claims.for_claimant(
        reference=filed.reference, principal_id=lab.sender_id
    )
    assert view.rejection_reason is RejectionReason.DAMAGE_PRESENT_BEFORE_PICKUP
    assert view.rejection_note


def test_the_support_conversation_belongs_to_the_claim() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    lab.claims.post_message(
        reference=filed.reference,
        author=ConversationAuthor.CLAIMANT,
        body="The box arrived crushed on one side.",
        author_principal_id=lab.sender_id,
    )
    lab.claims.post_message(
        reference=filed.reference,
        author=ConversationAuthor.SUPPORT,
        body="Thank you — we are reading the scans now.",
    )
    thread = lab.claims.conversation(
        reference=filed.reference, principal_id=lab.sender_id
    )
    assert len(thread) == 2
    assert thread[0].author is ConversationAuthor.CLAIMANT


def test_an_empty_message_is_refused() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    with pytest.raises(MessageBodyRequired):
        lab.claims.post_message(
            reference=filed.reference, author=ConversationAuthor.SUPPORT, body="   "
        )


def test_a_closed_claim_takes_no_more_messages() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    reviewed(lab, filed.reference)
    lab.claims.approve(
        reference=filed.reference, actor=operations(), compensation=iqd(1_000)
    )
    with pytest.raises(ClosedClaimTakesNoMessages):
        lab.claims.post_message(
            reference=filed.reference, author=ConversationAuthor.SUPPORT, body="hello"
        )


def test_a_stranger_cannot_read_the_conversation() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    with pytest.raises(NotYourClaim):
        lab.claims.conversation(reference=filed.reference, principal_id=uuid4())


def test_support_can_read_any_conversation() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    assert lab.claims.conversation(reference=filed.reference, actor=support()) == ()


def test_a_claimant_may_withdraw_their_own_claim() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    withdrawn = lab.claims.withdraw(
        reference=filed.reference, principal_id=lab.sender_id
    )
    assert withdrawn.status is ClaimStatus.WITHDRAWN


def test_a_stranger_cannot_withdraw_a_claim() -> None:
    lab = build_lab()
    filed = file_damage(lab)
    with pytest.raises(NotYourClaim):
        lab.claims.withdraw(reference=filed.reference, principal_id=uuid4())


def test_an_unknown_reference_is_not_found() -> None:
    lab = build_lab()
    with pytest.raises(ClaimNotFound):
        lab.claims.claim(reference="CLM-20260915-999999")


# ------------------------------------------------------------------ SEC-07


def test_the_driver_view_has_no_field_for_a_compensation_value() -> None:
    """Driver App v8 `incidentDone`: "No compensation or claim value is shown".

    Asserted on the shape rather than on one response, because a field that exists is a
    field a future serializer can expose.
    """
    fields = {f.name for f in dataclasses.fields(ClaimSummaryForDriver)}
    assert not {
        "compensation_amount",
        "amount",
        "value",
        "compensation",
    } & fields
    assert fields == {
        "reference",
        "kind",
        "status",
        "tracking_code",
        "submitted_at",
        "resolved_at",
    }


def test_a_driver_reading_an_approved_claim_sees_no_amount() -> None:
    lab = build_lab()
    filed = file_damage(
        lab, opened_by=ClaimOpenedBy.DRIVER, opened_by_principal_id=lab.driver_id
    )
    reviewed(lab, filed.reference)
    lab.claims.approve(
        reference=filed.reference, actor=operations(), compensation=iqd(500_000)
    )
    view = lab.claims.for_driver(reference=filed.reference)
    assert "500000" not in repr(view)
    assert view.status == "APPROVED"


def test_a_driver_is_never_permitted_to_see_a_value_even_with_another_role() -> None:
    """A driver who is also support is still a driver, and the promise is about that."""
    both = ClaimsActor(
        principal_id=uuid4(),
        roles=frozenset({ClaimsRole.LAST_MILE_DRIVER, ClaimsRole.SUPPORT}),
    )
    assert both.may_see_a_compensation_value is False
    assert support().may_see_a_compensation_value is True


# ------------------------------------------------------------------ CLM-06


def test_there_is_no_return_window_after_acceptance_at_the_door() -> None:
    """v6.3 — "that decision is final".

    A refusal rather than a missing route, so the absence reads as the decision it is.
    """
    lab = build_lab()
    with pytest.raises(NoReturnWindowAfterAcceptance) as caught:
        lab.claims.request_return(tracking_code=next_tracking_code())
    assert "no return window" in str(caught.value)


def test_a_claim_is_still_possible_after_acceptance() -> None:
    """No return window is not the same as no recourse — CLM-01 still applies."""
    lab = build_lab()
    filed = file_damage(lab, custody_boundary=CustodyBoundary.IN_HUDHUD_CUSTODY)
    assert filed.claim.status is ClaimStatus.SUBMITTED


def test_filing_and_approval_agree_about_where_liability_ended() -> None:
    """A claim must not be accepted and then refused for a reason knowable on day one."""
    for boundary in CustodyBoundary:
        lab = build_lab()
        if boundary in {
            CustodyBoundary.IN_HUDHUD_CUSTODY,
            CustodyBoundary.OPEN_BOX_AT_THE_DOOR,
        }:
            filed = file_damage(lab, custody_boundary=boundary)
            reviewed(lab, filed.reference)
            approved = lab.claims.approve(
                reference=filed.reference, actor=operations(), compensation=iqd(1_000)
            )
            assert approved.status is ClaimStatus.APPROVED, boundary
        else:
            with pytest.raises(LiabilityEndedAtTheDoor):
                file_damage(lab, custody_boundary=boundary)
