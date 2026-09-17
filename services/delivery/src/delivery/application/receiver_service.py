"""What the receiver can do about a parcel that is on its way to them.

v6.3 p.20 explains why this exists at all: the tracking link and the WhatsApp message
push the receiver towards the app *because* a stated time window and an exact pin turn a
failed attempt into a delivered one.

Three rules shape the code.

* A receiver may act on a parcel that is still live. Once the stop is closed there is
  nothing left to reschedule or re-pin, and a rating becomes possible instead.
* A rating is private (SEC-08). The rater and the note are stored; the courier is only
  ever handed :class:`CourierRatingSummary`, which has a field for neither.
* One rating per delivery. Customer App v3 `rateCourier` offers the rating once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from delivery.domain.entities import (
    CourierRating,
    CourierRatingSummary,
    ParcelIssueReport,
    ReceiverPreference,
)
from delivery.domain.errors import (
    NotThisDriversStop,
    RatingAlreadyGiven,
    RatingNotYetPossible,
    RatingOutOfRange,
    StopAlreadyClosed,
    StopNotFound,
)
from delivery.domain.value_objects import (
    DOOR_WAIT_SECONDS,
    EvidenceMediaRef,
    GeoPoint,
    IssueKind,
    IssueSource,
    RatingTag,
    StopStatus,
    TimeWindow,
)
from delivery.ports.repository import DeliveryUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class ArrivalView:
    """What the receiver is shown while the parcel is coming (CUS-10, CUS-11).

    No delivery code, no driver phone number, no receiver identity document: the
    tracking surface is public enough that none of those belong in it.
    """

    tracking_code: str
    status: StopStatus
    departed_at: datetime | None
    wait_minutes_at_door: int
    preference: ReceiverPreference | None


class ReceiverService:
    def __init__(self, unit_of_work: DeliveryUnitOfWork) -> None:
        self._uow = unit_of_work

    # --------------------------------------------------------------- CUS-11

    def set_handover_preference(
        self,
        *,
        tracking_code: str,
        principal_id: UUID | None = None,
        window: TimeWindow | None = None,
        address_line: str | None = None,
        landmark: str | None = None,
        geo: GeoPoint | None = None,
    ) -> ReceiverPreference:
        """A preferred window and an exact location, set while the parcel is live.

        Both parts are optional on their own — a receiver may pin the door without
        naming a window, or the reverse — but an empty call changes nothing and is
        refused rather than silently stored.
        """
        if window is None and address_line is None and landmark is None and geo is None:
            msg = "a handover preference must state a window, an address or a location"
            raise ValueError(msg)

        self._uow.begin()
        try:
            stop = self._live_stop(tracking_code)
            existing = self._uow.receiver.find_preference(tracking_code)
            if existing is None:
                preference = ReceiverPreference(
                    preference_id=uuid4(),
                    tracking_code=stop.tracking_code,
                    window=window,
                    address_line=address_line,
                    landmark=landmark,
                    geo=geo,
                    set_by_principal_id=principal_id,
                    updated_at=_now(),
                )
            else:
                preference = existing
                # A partial update leaves the parts the receiver did not resend alone.
                if window is not None:
                    preference.window = window
                if address_line is not None:
                    preference.address_line = address_line
                if landmark is not None:
                    preference.landmark = landmark
                if geo is not None:
                    preference.geo = geo
                preference.set_by_principal_id = principal_id
                preference.updated_at = _now()
                preference.version += 1
            self._uow.receiver.save_preference(preference)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return preference

    def preference_for(self, *, tracking_code: str) -> ReceiverPreference | None:
        self._uow.begin()
        try:
            found = self._uow.receiver.find_preference(tracking_code)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # --------------------------------------------------------------- CUS-12

    def report_issue(
        self,
        *,
        tracking_code: str,
        kind: IssueKind,
        detail: str | None = None,
        principal_id: UUID | None = None,
        media: tuple[EvidenceMediaRef, ...] = (),
    ) -> ParcelIssueReport:
        """A receiver reporting a problem.

        Delivery records the report and nothing more. Whether it becomes a claim is
        ADR-0012's Claims context to decide, not this one.
        """
        self._uow.begin()
        try:
            stop = self._stop(tracking_code)
            report = ParcelIssueReport(
                report_id=uuid4(),
                tracking_code=stop.tracking_code,
                kind=kind,
                source=IssueSource.RECEIVER,
                detail=detail,
                reported_by_principal_id=principal_id,
                media=media,
                reported_at=_now(),
            )
            self._uow.receiver.save_report(report)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return report

    # --------------------------------------------------------------- DRV-L21

    def raise_driver_incident(
        self,
        *,
        stop_id: UUID,
        driver_principal_id: UUID,
        kind: IssueKind,
        detail: str | None = None,
        media: tuple[EvidenceMediaRef, ...] = (),
    ) -> ParcelIssueReport:
        """Driver App v8 `lmIncident` — a damage or loss incident opened from a stop.

        Raising one does not change the stop: the parcel is still where it was, and the
        driver still has to end the visit one of the three ways. What this records is
        that something happened to it in their custody.
        """
        self._uow.begin()
        try:
            stop = self._uow.stops.get(stop_id)
            if stop is None:
                raise StopNotFound(str(stop_id))
            if stop.driver_principal_id != driver_principal_id:
                raise NotThisDriversStop(stop.tracking_code)
            report = ParcelIssueReport(
                report_id=uuid4(),
                tracking_code=stop.tracking_code,
                kind=kind,
                source=IssueSource.DRIVER,
                stop_id=stop.stop_id,
                detail=detail,
                reported_by_principal_id=driver_principal_id,
                media=media,
                reported_at=_now(),
            )
            self._uow.receiver.save_report(report)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return report

    def reports_for(self, *, tracking_code: str) -> tuple[ParcelIssueReport, ...]:
        self._uow.begin()
        try:
            found = self._uow.receiver.list_reports(tracking_code)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # --------------------------------------------------------- CUS-13/SEC-08

    def rate_courier(
        self,
        *,
        tracking_code: str,
        score: int,
        tags: tuple[RatingTag, ...] = (),
        note: str | None = None,
        principal_id: UUID | None = None,
    ) -> CourierRating:
        """Rate the courier who delivered. Once, and only after the handover."""
        if not isinstance(score, int) or isinstance(score, bool):
            msg = "a courier rating score must be a whole number"
            raise TypeError(msg)
        if not 1 <= score <= 5:
            raise RatingOutOfRange()

        self._uow.begin()
        try:
            stop = self._stop(tracking_code)
            if stop.status is not StopStatus.DELIVERED:
                # Nothing to rate until the courier actually handed the parcel over.
                raise RatingNotYetPossible(stop.status.value)
            if self._uow.ratings.find_for_tracking_code(tracking_code) is not None:
                raise RatingAlreadyGiven(tracking_code)
            rating = CourierRating(
                rating_id=uuid4(),
                courier_principal_id=stop.driver_principal_id,
                tracking_code=stop.tracking_code,
                score=score,
                tags=tags,
                note=note,
                rated_by_principal_id=principal_id,
                rated_at=_now(),
            )
            self._uow.ratings.save(rating)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return rating

    def rating_summary_for_courier(
        self, *, courier_principal_id: UUID
    ) -> CourierRatingSummary:
        """The only shape a courier's ratings leave this service in.

        SEC-08 — Customer App v3 `rateCourier`: "The courier never sees your name or
        your note." Neither is carried here, so there is no call site that could leak
        one by accident.
        """
        self._uow.begin()
        try:
            ratings = self._uow.ratings.list_for_courier(courier_principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        if not ratings:
            return CourierRatingSummary(
                courier_principal_id=courier_principal_id,
                rating_count=0,
                average_score=None,
            )
        tag_counts: dict[str, int] = {}
        for rating in ratings:
            for tag in rating.tags:
                tag_counts[tag.value] = tag_counts.get(tag.value, 0) + 1
        total = sum(rating.score for rating in ratings)
        return CourierRatingSummary(
            courier_principal_id=courier_principal_id,
            rating_count=len(ratings),
            # A rating average is a statistic, not money; a float is correct here and
            # nowhere near the integer minor units the payment side uses.
            average_score=round(total / len(ratings), 2),
            tag_counts=tag_counts,
        )

    # --------------------------------------------------------------- CUS-10

    def arrival_view(self, *, tracking_code: str) -> ArrivalView:
        self._uow.begin()
        try:
            stop = self._stop(tracking_code)
            preference = self._uow.receiver.find_preference(tracking_code)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        return ArrivalView(
            tracking_code=stop.tracking_code,
            status=stop.status,
            departed_at=stop.departed_at,
            wait_minutes_at_door=DOOR_WAIT_SECONDS // 60,
            preference=preference,
        )

    # ------------------------------------------------------------- internals

    def _stop(self, tracking_code: str):
        stop = self._uow.stops.find_by_tracking_code(tracking_code)
        if stop is None:
            raise StopNotFound(tracking_code)
        return stop

    def _live_stop(self, tracking_code: str):
        stop = self._uow.stops.find_live_by_tracking_code(tracking_code)
        if stop is None:
            closed = self._uow.stops.find_by_tracking_code(tracking_code)
            if closed is not None:
                raise StopAlreadyClosed(closed.status.value)
            raise StopNotFound(tracking_code)
        return stop
