"""Money at the door, and the three ways a visit ends.

The rule that holds this file together is v6.3 p.31: a COD parcel is not Delivered
unless payment was actually collected. Around it sit the card rules from Driver App v8
and the custody rule from p.28 and p.34 — a refusal and a failed attempt both leave the
parcel with HUDHUD and collect nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from delivery_fixtures import (
    TEST_CODE,
    authorize_at_the_door,
    build_lab,
    drive_to_the_door,
    iqd,
    scan_parcel,
)

from delivery.domain.errors import (
    NextAttemptAlreadyDecided,
    NothingToCollect,
    NotThisDriversStop,
    NotVerified,
    OnlyOperationsDecidesTheNextAttempt,
    PaymentAlreadyRecorded,
    PaymentRequiredBeforeHandover,
    PosProofRequired,
    StopTransitionNotAllowed,
    WaitNotOver,
)
from delivery.domain.money import Money
from delivery.domain.value_objects import (
    DOOR_WAIT_SECONDS,
    HOLD_DAYS,
    EvidenceMediaRef,
    FailureReason,
    InspectionOutcome,
    IssueKind,
    IssueSource,
    NextAttemptDecision,
    PaymentMethod,
    PaymentOutcome,
    RefusalReason,
    StopStatus,
)

# ------------------------------------------------------------------ payment


def test_a_prepaid_parcel_collects_nothing() -> None:
    """v6.3 p.31."""
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    result = lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.payment.outcome is PaymentOutcome.NOTHING_TO_COLLECT
    assert result.cash_custody_delta == Money.zero()


def test_prepaid_confirmation_is_refused_on_a_cod_parcel() -> None:
    lab = build_lab()
    stop = _cod_parcel(lab)
    authorize_at_the_door(lab, stop)
    with pytest.raises(PaymentRequiredBeforeHandover):
        lab.payments.confirm_prepaid(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )


def test_cash_enters_the_drivers_custody() -> None:
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    result = lab.payments.collect_cash(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.payment.enters_driver_cash_custody is True
    assert result.cash_custody_delta == iqd(25_000)


def test_the_amount_comes_from_the_parcel_not_from_the_driver() -> None:
    """A driver-typed figure could differ from what the merchant charged."""
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    result = lab.payments.collect_cash(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.payment.amount == iqd(25_000)


def test_a_card_payment_never_enters_the_drivers_custody() -> None:
    """Driver App v8 `lmPayApproved` — "this amount is not added to your cash on hand"."""
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000, method=PaymentMethod.POS_CARD)
    authorize_at_the_door(lab, stop)
    result = lab.payments.record_card_approval(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        pos_reference="POS-84213",
    )
    assert result.payment.enters_driver_cash_custody is False
    assert result.cash_custody_delta == Money.zero()


def test_a_card_payment_needs_proof() -> None:
    """DRV-L14 — "PROOF OF PAYMENT — ONE IS REQUIRED"."""
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000, method=PaymentMethod.POS_CARD)
    authorize_at_the_door(lab, stop)
    with pytest.raises(PosProofRequired):
        lab.payments.record_card_approval(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )


def test_a_receipt_photo_is_proof_too() -> None:
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000, method=PaymentMethod.POS_CARD)
    authorize_at_the_door(lab, stop)
    result = lab.payments.record_card_approval(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        pos_receipt=EvidenceMediaRef(bucket="evidence", key="receipt.jpg"),
    )
    assert result.payment.has_pos_proof is True


def test_a_declined_card_leaves_room_for_cash() -> None:
    """DRV-L13 — the parcel is still at the door and still payable."""
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000, method=PaymentMethod.POS_CARD)
    authorize_at_the_door(lab, stop)
    declined = lab.payments.record_card_decline(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert declined.outcome is PaymentOutcome.DECLINED
    fallback = lab.payments.collect_cash(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert fallback.payment.enters_driver_cash_custody is True


def test_a_parcel_is_not_paid_twice() -> None:
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    lab.payments.collect_cash(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    with pytest.raises(PaymentAlreadyRecorded):
        lab.payments.collect_cash(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )


def test_there_is_nothing_to_collect_on_a_prepaid_parcel() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    with pytest.raises(NothingToCollect):
        lab.payments.collect_cash(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )


def test_money_is_never_a_float() -> None:
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    amount = lab.payments.collect_cash(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    ).payment.amount
    assert isinstance(amount.minor_units, int)
    assert not isinstance(amount.minor_units, bool)


# ----------------------------------------------------------------- handover


def test_a_cod_parcel_is_not_delivered_unless_it_was_paid() -> None:
    """v6.3 p.31 — the rule this whole file exists for."""
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    with pytest.raises(PaymentRequiredBeforeHandover):
        lab.outcomes.complete_delivery(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )
    assert lab.doorstep.get_stop(stop.stop_id).status is not StopStatus.DELIVERED


def test_a_paid_cod_parcel_is_delivered_and_publishes_two_facts() -> None:
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    lab.payments.collect_cash(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    result = lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.stop.status is StopStatus.DELIVERED
    assert result.cod_event_id is not None
    published = lab.uow.outbox.list_for_aggregate(stop.stop_id)
    types = [record.event_type for record in published]
    assert "delivery.fact.delivered" in types
    assert "delivery.fact.cod_collected" in types


def test_a_prepaid_delivery_publishes_no_cod_fact() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    result = lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.cod_event_id is None
    types = [r.event_type for r in lab.uow.outbox.list_for_aggregate(stop.stop_id)]
    assert "delivery.fact.cod_collected" not in types


def test_an_unverified_parcel_is_not_delivered() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    with pytest.raises(NotVerified):
        lab.outcomes.complete_delivery(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )


def test_another_driver_cannot_complete_this_delivery() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    with pytest.raises(NotThisDriversStop):
        lab.outcomes.complete_delivery(
            stop_id=stop.stop_id, driver_principal_id=uuid4()
        )


def test_each_published_fact_has_its_own_aggregate_version() -> None:
    """Two facts sharing a version would collide on the ordering key consumers use."""
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    lab.payments.collect_cash(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    versions = [r.aggregate_version for r in lab.uow.outbox.list_for_aggregate(stop.stop_id)]
    assert len(versions) == len(set(versions))


def test_a_delivered_stop_cannot_be_delivered_again() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    with pytest.raises(StopTransitionNotAllowed):
        lab.outcomes.complete_delivery(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )


# ------------------------------------------------------------------ refusal


def test_a_refusal_collects_nothing_and_keeps_custody() -> None:
    """v6.3 p.34."""
    lab = build_lab()
    stop = _cod_parcel(lab, amount=25_000)
    authorize_at_the_door(lab, stop)
    result = lab.outcomes.record_refusal(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        reason=RefusalReason.CHANGED_MIND,
    )
    assert result.stop.status is StopStatus.REFUSED
    assert result.stop.in_driver_custody is True
    assert lab.payments.payment_for(stop_id=stop.stop_id) is None


def test_a_refusal_reason_is_optional() -> None:
    """Driver App v8 `lmRefuse` — "Reason is optional"."""
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    result = lab.outcomes.record_refusal(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.stop.refusal_reason is None
    assert result.attempt.reason is FailureReason.RECEIVER_REFUSED


def test_a_refusal_after_opening_keeps_the_inspection_outcome() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, open_box_allowed=True)
    drive_to_the_door(lab, stop)
    lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    lab.doorstep.record_inspection(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        outcome=InspectionOutcome.OPEN_BOX_REFUSED,
    )
    result = lab.outcomes.record_refusal(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.stop.inspection_outcome is InspectionOutcome.OPEN_BOX_REFUSED


def test_a_sealed_refusal_invents_no_inspection() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    result = lab.outcomes.record_refusal(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.stop.inspection_outcome is None


# ------------------------------------------------------------ failed attempt


def test_absence_cannot_be_recorded_before_the_ten_minutes() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    lab.doorstep.start_wait(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    with pytest.raises(WaitNotOver):
        lab.outcomes.record_absence(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )


def test_absence_after_the_wait_closes_the_stop_and_keeps_custody() -> None:
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    result = lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.stop.status is StopStatus.FAILED
    assert result.stop.in_driver_custody is True
    assert result.attempt.reason is FailureReason.ABSENT_AFTER_WAIT


def test_a_failed_attempt_publishes_its_fact() -> None:
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    types = [r.event_type for r in lab.uow.outbox.list_for_aggregate(stop.stop_id)]
    assert "delivery.fact.attempt_failed" in types


def test_a_verification_failure_closes_the_stop() -> None:
    """DRV-L08."""
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    result = lab.outcomes.record_verification_failure(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.attempt.reason is FailureReason.VERIFICATION_NOT_COMPLETED


# ------------------------------------------------------------------ OPS-08


def test_a_new_failed_attempt_awaits_operations() -> None:
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    result = lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert result.attempt.awaits_operations is True
    assert result.attempt.attempt_id in {
        a.attempt_id for a in lab.outcomes.attempts_awaiting_operations()
    }


def test_the_driver_does_not_decide_the_next_attempt() -> None:
    """Driver App v8 `lmFailed` — "Held for next attempt — decided by operations"."""
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    result = lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    with pytest.raises(OnlyOperationsDecidesTheNextAttempt):
        lab.outcomes.decide_next_attempt(
            attempt_id=result.attempt.attempt_id,
            decision=NextAttemptDecision.RETRY,
            decider_principal_id=lab.driver_id,
            actor_is_operations=False,
        )


def test_operations_decides_once() -> None:
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    result = lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    operator = uuid4()
    decided = lab.outcomes.decide_next_attempt(
        attempt_id=result.attempt.attempt_id,
        decision=NextAttemptDecision.RETRY,
        decider_principal_id=operator,
        actor_is_operations=True,
    )
    assert decided.next_attempt_decision is NextAttemptDecision.RETRY
    assert decided.decided_by_actor_id == operator
    assert decided.awaits_operations is False
    with pytest.raises(NextAttemptAlreadyDecided):
        lab.outcomes.decide_next_attempt(
            attempt_id=result.attempt.attempt_id,
            decision=NextAttemptDecision.RETURN_TO_MERCHANT,
            decider_principal_id=operator,
            actor_is_operations=True,
        )


# ------------------------------------------------------------------ helpers


def _cod_parcel(lab, *, amount: int = 25_000, method=PaymentMethod.CASH):
    return scan_parcel(
        lab, cod_amount=iqd(amount), payment_method_expected=method
    )


def _stop_waiting_past_ten_minutes(lab):
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    lab.doorstep.start_wait(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    stored = lab.uow.stops.get(stop.stop_id)
    stored.wait_started_at = datetime.now(tz=UTC) - timedelta(
        seconds=DOOR_WAIT_SECONDS + 1
    )
    lab.uow.stops.save(stored)
    return stop


# --------------------------------------------------- DRV-L20: the three days


def test_the_hold_is_three_days() -> None:
    """v6.3 p.29 records this as a Confirmed decision, so it is a constant."""
    assert HOLD_DAYS == 3


def test_a_fresh_failed_attempt_is_not_past_its_hold() -> None:
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    assert lab.outcomes.attempts_past_their_hold() == ()


def test_the_hold_expires_three_days_after_the_attempt() -> None:
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    attempt = lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    ).attempt
    assert attempt.hold_expires_at() == attempt.recorded_at + timedelta(days=3)
    assert attempt.hold_is_over(attempt.recorded_at + timedelta(days=3))
    assert not attempt.hold_is_over(
        attempt.recorded_at + timedelta(days=3) - timedelta(seconds=1)
    )


def test_a_parcel_still_held_after_three_days_returns_to_the_merchant() -> None:
    """v6.3 p.29 — the rule that replaced the three-attempt limit."""
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    operator = uuid4()
    returned = lab.outcomes.return_parcels_past_their_hold(
        actor_principal_id=operator,
        moment=datetime.now(tz=UTC) + timedelta(days=3, seconds=1),
    )
    assert len(returned) == 1
    assert returned[0].next_attempt_decision is NextAttemptDecision.RETURN_TO_MERCHANT
    assert returned[0].decided_by_actor_id == operator


def test_the_sweep_leaves_a_parcel_operations_already_decided_alone() -> None:
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    attempt = lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    ).attempt
    lab.outcomes.decide_next_attempt(
        attempt_id=attempt.attempt_id,
        decision=NextAttemptDecision.RETRY,
        decider_principal_id=uuid4(),
        actor_is_operations=True,
    )
    returned = lab.outcomes.return_parcels_past_their_hold(
        actor_principal_id=uuid4(),
        moment=datetime.now(tz=UTC) + timedelta(days=30),
    )
    assert returned == ()


def test_the_sweep_is_idempotent() -> None:
    """Running it twice must not re-decide what it already returned."""
    lab = build_lab()
    stop = _stop_waiting_past_ten_minutes(lab)
    lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    later = datetime.now(tz=UTC) + timedelta(days=4)
    first = lab.outcomes.return_parcels_past_their_hold(
        actor_principal_id=uuid4(), moment=later
    )
    second = lab.outcomes.return_parcels_past_their_hold(
        actor_principal_id=uuid4(), moment=later
    )
    assert len(first) == 1
    assert second == ()


# ------------------------------------------------------- DRV-L21: incidents


def test_a_driver_can_open_an_incident_from_a_stop() -> None:
    """Driver App v8 `lmIncident`."""
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    report = lab.receiver.raise_driver_incident(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        kind=IssueKind.DAMAGED_IN_CUSTODY,
        detail="Dropped while unloading",
    )
    assert report.source is IssueSource.DRIVER
    assert report.stop_id == stop.stop_id
    assert report.tracking_code == stop.tracking_code


def test_an_incident_does_not_end_the_visit() -> None:
    """The parcel is still at the door and the visit still has to end one of three ways."""
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    lab.receiver.raise_driver_incident(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        kind=IssueKind.DAMAGED_IN_CUSTODY,
    )
    assert lab.doorstep.get_stop(stop.stop_id).status is StopStatus.ARRIVED


def test_another_driver_cannot_open_an_incident_on_this_stop() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    with pytest.raises(NotThisDriversStop):
        lab.receiver.raise_driver_incident(
            stop_id=stop.stop_id,
            driver_principal_id=uuid4(),
            kind=IssueKind.LOST_IN_CUSTODY,
        )


def test_a_driver_incident_and_a_receiver_report_sit_side_by_side() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    lab.receiver.raise_driver_incident(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        kind=IssueKind.DAMAGED_IN_CUSTODY,
    )
    lab.receiver.report_issue(
        tracking_code=stop.tracking_code, kind=IssueKind.DAMAGED
    )
    reports = lab.receiver.reports_for(tracking_code=stop.tracking_code)
    assert {r.source for r in reports} == {IssueSource.DRIVER, IssueSource.RECEIVER}
