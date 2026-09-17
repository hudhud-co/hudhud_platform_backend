"""The doorstep sequence: custody, the ten minutes, verification, seal, inspection.

Every rule here comes from the v6.3 delivery chapter or from the Driver App's own
guards, and each test names the one it is holding.
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
    next_tracking_code,
    scan_parcel,
)

from delivery.domain.errors import (
    BrokenSealStopsTheHandover,
    DeliveryCodeIncorrect,
    DeliveryCodeNotSet,
    IdFallbackIsOnlyForTheNamedReceiver,
    NoNamedReceiverOnThisParcel,
    NotThisDriversStop,
    OpenBoxNotAllowed,
    ParcelAlreadyOnAManifest,
    PhotographyNotEnabled,
    PhotoRequiredAfterInspection,
    PhotoRequiredBeforeOpening,
    SealMustBeCheckedFirst,
    StopTransitionNotAllowed,
    TooManyCodeAttempts,
    WaitNotOver,
    WaitNotStarted,
)
from delivery.domain.value_objects import (
    DOOR_WAIT_SECONDS,
    EvidenceMediaRef,
    InspectionOutcome,
    PaymentMethod,
    PhotoStage,
    SealCheckOutcome,
    StopStatus,
    VerificationMethod,
    VerificationOutcome,
)

# ------------------------------------------------------------------ custody


def test_the_scan_transfers_custody() -> None:
    """v6.3 p.26 — "that scan transfers custody", so it is recorded, not intended."""
    lab = build_lab()
    stop = scan_parcel(lab)
    assert stop.status is StopStatus.IN_CUSTODY
    assert stop.custody_taken_at is not None
    assert stop.in_driver_custody is True


def test_a_driver_keeps_one_open_manifest() -> None:
    """v6.3 p.25 — a driver carries one round at a time."""
    lab = build_lab()
    again = lab.doorstep.open_manifest(
        driver_principal_id=lab.driver_id, hub_id=lab.hub_id
    )
    assert again.manifest_id == lab.manifest_id


def test_a_parcel_cannot_be_on_two_manifests_at_once() -> None:
    lab = build_lab()
    code = next_tracking_code()
    scan_parcel(lab, tracking_code=code)
    with pytest.raises(ParcelAlreadyOnAManifest):
        scan_parcel(lab, tracking_code=code)


def test_a_parcel_can_be_rescanned_after_a_failed_attempt() -> None:
    """A retry is a new stop, not a resurrection of the closed one."""
    lab = build_lab()
    code = next_tracking_code()
    first = scan_parcel(lab, tracking_code=code)
    drive_to_the_door(lab, first)
    lab.doorstep.start_wait(stop_id=first.stop_id, driver_principal_id=lab.driver_id)
    _record_absence_after_the_wait(lab, first)
    second = scan_parcel(lab, tracking_code=code)
    assert second.stop_id != first.stop_id


def test_another_driver_cannot_act_on_this_stop() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    with pytest.raises(NotThisDriversStop):
        lab.doorstep.record_arrival(
            stop_id=stop.stop_id, driver_principal_id=uuid4()
        )


# ------------------------------------------------------------------ the wait


def test_the_wait_is_ten_minutes() -> None:
    """v6.3 p.26 and Customer App v3 `waitRuleBody` agree, so it is a constant."""
    assert DOOR_WAIT_SECONDS == 600


def test_a_failure_cannot_be_recorded_before_the_wait_starts() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    arrived = drive_to_the_door(lab, stop)
    with pytest.raises(WaitNotStarted):
        lab.doorstep.assert_wait_is_over(stop=arrived, moment=datetime.now(tz=UTC))


def test_a_failure_cannot_be_recorded_while_the_wait_runs() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    waiting = lab.doorstep.start_wait(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    with pytest.raises(WaitNotOver):
        lab.doorstep.assert_wait_is_over(
            stop=waiting, moment=waiting.wait_started_at + timedelta(seconds=599)
        )


def test_the_wait_is_over_at_exactly_ten_minutes() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    waiting = lab.doorstep.start_wait(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    moment = waiting.wait_started_at + timedelta(seconds=DOOR_WAIT_SECONDS)
    lab.doorstep.assert_wait_is_over(stop=waiting, moment=moment)


def test_the_remaining_seconds_are_reported_for_the_app_timer() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    lab.doorstep.start_wait(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    remaining = lab.doorstep.wait_remaining_seconds(stop_id=stop.stop_id)
    assert 0 < remaining <= DOOR_WAIT_SECONDS


# ------------------------------------------------------------ verification


def test_anyone_holding_the_code_may_receive_the_parcel() -> None:
    """v6.3 p.26 — the code authorizes, not the identity."""
    lab = build_lab()
    stop = scan_parcel(lab, named_receiver=None)
    drive_to_the_door(lab, stop)
    result = lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    assert result.authorized is True
    assert result.stop.verified_by_method is VerificationMethod.DELIVERY_CODE


def test_a_wrong_code_is_recorded_as_a_mismatch() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    with pytest.raises(DeliveryCodeIncorrect):
        lab.doorstep.verify_with_code(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code="111111"
        )
    attempts = lab.doorstep.verifications_for(stop_id=stop.stop_id)
    assert attempts[-1].outcome is VerificationOutcome.CODE_MISMATCH


def test_the_attempt_counter_survives_a_rejected_code() -> None:
    """Otherwise the limit could be reset by simply guessing again."""
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    for _ in range(2):
        with pytest.raises(DeliveryCodeIncorrect):
            lab.doorstep.verify_with_code(
                stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code="111111"
            )
    assert lab.doorstep.get_stop(stop.stop_id).code_attempt_count == 2


def test_guessing_stops_at_the_attempt_limit() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    drive_to_the_door(lab, stop)
    for _ in range(5):
        with pytest.raises(DeliveryCodeIncorrect):
            lab.doorstep.verify_with_code(
                stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code="111111"
            )
    with pytest.raises(TooManyCodeAttempts):
        lab.doorstep.verify_with_code(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
        )


def test_a_parcel_with_no_code_cannot_be_verified_by_code() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, code=None, named_receiver="Zaid Al-Rawi")
    drive_to_the_door(lab, stop)
    with pytest.raises(DeliveryCodeNotSet):
        lab.doorstep.verify_with_code(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
        )


def test_the_id_fallback_needs_a_named_receiver() -> None:
    """v6.3 p.26 — "Anyone else cannot take this parcel without the code"."""
    lab = build_lab()
    stop = scan_parcel(lab, named_receiver=None)
    drive_to_the_door(lab, stop)
    with pytest.raises(NoNamedReceiverOnThisParcel):
        lab.doorstep.verify_with_named_receiver_id(
            stop_id=stop.stop_id,
            driver_principal_id=lab.driver_id,
            id_matches_named_receiver=True,
        )


def test_the_id_fallback_refuses_anyone_but_the_named_receiver() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, named_receiver="Zaid Al-Rawi")
    drive_to_the_door(lab, stop)
    with pytest.raises(IdFallbackIsOnlyForTheNamedReceiver):
        lab.doorstep.verify_with_named_receiver_id(
            stop_id=stop.stop_id,
            driver_principal_id=lab.driver_id,
            id_matches_named_receiver=False,
        )


def test_verification_is_refused_before_the_driver_arrives() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    with pytest.raises(StopTransitionNotAllowed):
        lab.doorstep.verify_with_code(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
        )


# ------------------------------------------------------------------ the seal


def test_a_broken_seal_stops_the_handover() -> None:
    """v6.3 p.14 — the merchant's parcel seal, not the hub's batch seal."""
    lab = build_lab(code_length=6)
    stop = scan_parcel(lab, packaging_seal_code="SEAL-001")
    drive_to_the_door(lab, stop)
    lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    with pytest.raises(BrokenSealStopsTheHandover):
        lab.doorstep.check_parcel_seal(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id, intact=False
        )
    assert lab.doorstep.get_stop(stop.stop_id).seal_outcome is SealCheckOutcome.BROKEN


def test_a_sealed_parcel_must_be_checked_before_inspection() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, packaging_seal_code="SEAL-002", open_box_allowed=True)
    drive_to_the_door(lab, stop)
    lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    with pytest.raises(SealMustBeCheckedFirst):
        lab.doorstep.record_inspection(
            stop_id=stop.stop_id,
            driver_principal_id=lab.driver_id,
            outcome=InspectionOutcome.OPEN_BOX_KEPT,
        )


def test_an_unsealed_parcel_needs_no_seal_check() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    assert stop.requires_seal_check is False


# --------------------------------------------------------------- inspection


def test_open_box_needs_the_merchant_to_have_enabled_it() -> None:
    """v6.3 p.35, p.36 — otherwise the receiver accepts or refuses sealed."""
    lab = build_lab()
    stop = scan_parcel(lab, open_box_allowed=False)
    drive_to_the_door(lab, stop)
    lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    with pytest.raises(OpenBoxNotAllowed):
        lab.doorstep.record_inspection(
            stop_id=stop.stop_id,
            driver_principal_id=lab.driver_id,
            outcome=InspectionOutcome.OPEN_BOX_KEPT,
        )


def test_open_box_with_photography_needs_a_photo_before_opening() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, open_box_allowed=True, photo_documentation=True)
    drive_to_the_door(lab, stop)
    lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    with pytest.raises(PhotoRequiredBeforeOpening):
        lab.doorstep.record_inspection(
            stop_id=stop.stop_id,
            driver_principal_id=lab.driver_id,
            outcome=InspectionOutcome.OPEN_BOX_KEPT,
        )


def test_a_kept_open_box_needs_a_photo_after_inspection_before_payment() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, open_box_allowed=True, photo_documentation=True)
    drive_to_the_door(lab, stop)
    lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    lab.doorstep.attach_photo(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        stage=PhotoStage.BEFORE_OPENING,
        media=EvidenceMediaRef(bucket="evidence", key="before.jpg"),
    )
    authorized = lab.doorstep.record_inspection(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        outcome=InspectionOutcome.OPEN_BOX_KEPT,
    )
    with pytest.raises(PhotoRequiredAfterInspection):
        lab.doorstep.assert_ready_for_payment(stop=authorized)


def test_photography_is_refused_when_the_merchant_did_not_buy_it() -> None:
    """v6.3 p.14 — without the add-on, no photo is taken at any stage."""
    lab = build_lab()
    stop = scan_parcel(lab, photo_documentation=False)
    drive_to_the_door(lab, stop)
    with pytest.raises(PhotographyNotEnabled):
        lab.doorstep.attach_photo(
            stop_id=stop.stop_id,
            driver_principal_id=lab.driver_id,
            stage=PhotoStage.AFTER_INSPECTION,
            media=EvidenceMediaRef(bucket="evidence", key="after.jpg"),
        )


def test_a_sealed_acceptance_is_the_default_path() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorized = authorize_at_the_door(lab, stop)
    assert authorized.inspection_outcome is InspectionOutcome.SEALED_ACCEPTED
    lab.doorstep.assert_ready_for_payment(stop=authorized)


def test_payment_is_refused_before_an_inspection_is_recorded() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, cod_amount=iqd(25_000),
                       payment_method_expected=PaymentMethod.CASH)
    drive_to_the_door(lab, stop)
    result = lab.doorstep.verify_with_code(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id, code=TEST_CODE
    )
    with pytest.raises(StopTransitionNotAllowed):
        lab.doorstep.assert_ready_for_payment(stop=result.stop)


def _record_absence_after_the_wait(lab, stop) -> None:
    """Close a stop as absent by moving the wait's start back past ten minutes."""
    stored = lab.uow.stops.get(stop.stop_id)
    stored.wait_started_at = datetime.now(tz=UTC) - timedelta(
        seconds=DOOR_WAIT_SECONDS + 1
    )
    lab.uow.stops.save(stored)
    lab.outcomes.record_absence(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
