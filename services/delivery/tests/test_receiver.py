"""What the receiver can do: the handover preference, an issue report, and a rating.

The privacy rule is the one to watch. Customer App v3 `rateCourier` promises "Your
rating stays private" and "The courier never sees your name or your note", so the
courier-facing read model has a field for neither and these tests say so.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from delivery_fixtures import authorize_at_the_door, build_lab, scan_parcel

from delivery.domain.entities import CourierRatingSummary
from delivery.domain.errors import (
    RatingAlreadyGiven,
    RatingNotYetPossible,
    RatingOutOfRange,
    StopAlreadyClosed,
    StopNotFound,
)
from delivery.domain.value_objects import (
    EvidenceMediaRef,
    GeoPoint,
    IssueKind,
    RatingTag,
    StopStatus,
    TimeWindow,
)

# ------------------------------------------------------------------ CUS-11


def test_a_receiver_can_state_a_window_and_an_exact_location() -> None:
    """v6.3 p.20 — the reason the tracking link pushes the app."""
    lab = build_lab()
    stop = scan_parcel(lab)
    preference = lab.receiver.set_handover_preference(
        tracking_code=stop.tracking_code,
        window=TimeWindow(starts_at_hour=16, ends_at_hour=20),
        geo=GeoPoint(latitude=Decimal("33.315"), longitude=Decimal("44.366")),
    )
    assert preference.window.starts_at_hour == 16
    assert preference.has_exact_location is True


def test_a_window_alone_is_enough() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    preference = lab.receiver.set_handover_preference(
        tracking_code=stop.tracking_code,
        window=TimeWindow(starts_at_hour=9, ends_at_hour=12),
    )
    assert preference.has_exact_location is False


def test_an_empty_preference_is_refused_rather_than_stored() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    with pytest.raises(ValueError, match="window, an address or a location"):
        lab.receiver.set_handover_preference(tracking_code=stop.tracking_code)


def test_a_second_call_updates_only_what_was_resent() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    lab.receiver.set_handover_preference(
        tracking_code=stop.tracking_code,
        window=TimeWindow(starts_at_hour=16, ends_at_hour=20),
        landmark="Behind the blue mosque",
    )
    updated = lab.receiver.set_handover_preference(
        tracking_code=stop.tracking_code,
        geo=GeoPoint(latitude=Decimal("33.315"), longitude=Decimal("44.366")),
    )
    assert updated.landmark == "Behind the blue mosque"
    assert updated.window.ends_at_hour == 20
    assert updated.has_exact_location is True


def test_a_window_must_lie_within_a_day() -> None:
    with pytest.raises(ValueError, match="end after it starts"):
        TimeWindow(starts_at_hour=20, ends_at_hour=16)


def test_a_preference_cannot_be_set_on_a_closed_parcel() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    with pytest.raises(StopAlreadyClosed):
        lab.receiver.set_handover_preference(
            tracking_code=stop.tracking_code,
            window=TimeWindow(starts_at_hour=9, ends_at_hour=12),
        )


def test_an_unknown_parcel_is_not_found() -> None:
    lab = build_lab()
    with pytest.raises(StopNotFound):
        lab.receiver.set_handover_preference(
            tracking_code="SHP-20260915-999999", landmark="nowhere"
        )


# ------------------------------------------------------------------ CUS-12


def test_a_receiver_can_report_a_problem() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    report = lab.receiver.report_issue(
        tracking_code=stop.tracking_code,
        kind=IssueKind.DAMAGED,
        detail="The box arrived crushed",
        media=(EvidenceMediaRef(bucket="evidence", key="damage.jpg"),),
    )
    assert report.kind is IssueKind.DAMAGED
    assert len(report.media) == 1
    assert lab.receiver.reports_for(tracking_code=stop.tracking_code) == (report,)


def test_a_report_can_be_made_after_delivery() -> None:
    """Damage is usually found after the box is open, not at the door."""
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    report = lab.receiver.report_issue(
        tracking_code=stop.tracking_code, kind=IssueKind.MISSING_ITEMS
    )
    assert report.report_id is not None


# ------------------------------------------------------------ CUS-13/SEC-08


def test_a_courier_is_rated_after_the_handover() -> None:
    lab = build_lab()
    stop = _delivered(lab)
    rating = lab.receiver.rate_courier(
        tracking_code=stop.tracking_code,
        score=5,
        tags=(RatingTag.POLITE, RatingTag.ON_TIME),
        note="Waited patiently",
        principal_id=uuid4(),
    )
    assert rating.courier_principal_id == lab.driver_id
    assert rating.score == 5


def test_a_courier_cannot_be_rated_before_the_handover() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    with pytest.raises(RatingNotYetPossible):
        lab.receiver.rate_courier(tracking_code=stop.tracking_code, score=5)


def test_a_parcel_is_rated_once() -> None:
    lab = build_lab()
    stop = _delivered(lab)
    lab.receiver.rate_courier(tracking_code=stop.tracking_code, score=4)
    with pytest.raises(RatingAlreadyGiven):
        lab.receiver.rate_courier(tracking_code=stop.tracking_code, score=1)


def test_a_score_outside_one_to_five_is_refused() -> None:
    lab = build_lab()
    stop = _delivered(lab)
    for score in (0, 6, -1):
        with pytest.raises(RatingOutOfRange):
            lab.receiver.rate_courier(tracking_code=stop.tracking_code, score=score)


def test_a_boolean_is_not_a_score() -> None:
    lab = build_lab()
    stop = _delivered(lab)
    with pytest.raises(TypeError):
        lab.receiver.rate_courier(tracking_code=stop.tracking_code, score=True)


def test_the_courier_summary_carries_no_rater_and_no_note() -> None:
    """SEC-08 — the promise the customer was given when they rated."""
    fields = set(CourierRatingSummary.__dataclass_fields__)
    assert "note" not in fields
    assert "rated_by_principal_id" not in fields
    assert fields == {
        "courier_principal_id",
        "rating_count",
        "average_score",
        "tag_counts",
    }


def test_the_summary_aggregates_without_exposing_anything() -> None:
    lab = build_lab()
    first = _delivered(lab)
    lab.receiver.rate_courier(
        tracking_code=first.tracking_code,
        score=5,
        tags=(RatingTag.POLITE,),
        note="Secret note",
        principal_id=uuid4(),
    )
    second = _delivered(lab)
    lab.receiver.rate_courier(
        tracking_code=second.tracking_code,
        score=3,
        tags=(RatingTag.POLITE, RatingTag.LATE),
        note="Another secret",
        principal_id=uuid4(),
    )
    summary = lab.receiver.rating_summary_for_courier(
        courier_principal_id=lab.driver_id
    )
    assert summary.rating_count == 2
    assert summary.average_score == 4.0
    assert summary.tag_counts == {"POLITE": 2, "LATE": 1}
    assert "Secret note" not in repr(summary)


def test_an_unrated_courier_has_no_average_rather_than_a_zero() -> None:
    """A zero would read as a bad courier; no ratings is not a rating of nothing."""
    lab = build_lab()
    summary = lab.receiver.rating_summary_for_courier(courier_principal_id=uuid4())
    assert summary.rating_count == 0
    assert summary.average_score is None


# ------------------------------------------------------------------ CUS-10


def test_the_arrival_view_shows_the_wait_rule_and_no_code() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    lab.outcomes.announce_departure(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    view = lab.receiver.arrival_view(tracking_code=stop.tracking_code)
    assert view.status is StopStatus.EN_ROUTE
    assert view.wait_minutes_at_door == 10
    assert view.departed_at is not None
    assert "code" not in repr(view).lower() or "delivery_code" not in repr(view)


def test_the_arrival_view_carries_the_preference_the_receiver_set() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    lab.receiver.set_handover_preference(
        tracking_code=stop.tracking_code, landmark="Second floor, green door"
    )
    view = lab.receiver.arrival_view(tracking_code=stop.tracking_code)
    assert view.preference.landmark == "Second floor, green door"


def _delivered(lab):
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    return stop
