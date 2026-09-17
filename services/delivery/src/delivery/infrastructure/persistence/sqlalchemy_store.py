"""PostgreSQL unit of work for the Delivery service.

Writes are staged during the transaction and flushed on commit with a version-conditional
UPDATE, so two drivers' devices racing on the same stop produce a `StaleDeliveryRecord`
rather than one silently overwriting the other. Nothing here reads or writes another
service's tables.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from delivery.domain.entities import (
    CourierRating,
    DeliveryManifest,
    DeliveryStop,
    FailedAttempt,
    ParcelIssueReport,
    ParcelSettings,
    PaymentRecord,
    PhotoEvidence,
    ReceiverPreference,
    VerificationAttempt,
)
from delivery.domain.errors import StaleDeliveryRecord
from delivery.domain.messaging import (
    InboxRecord,
    InboxStatus,
    OutboxRecord,
    OutboxStatus,
)
from delivery.domain.money import Currency, Money
from delivery.domain.value_objects import (
    CLOSED_STATUSES,
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
from delivery.infrastructure.persistence.models import (
    CourierRatingRow,
    DeliveryManifestRow,
    DeliveryStopRow,
    FailedAttemptRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    ParcelIssueReportRow,
    PaymentRecordRow,
    PhotoEvidenceRow,
    ReceiverPreferenceRow,
    VerificationAttemptRow,
)

_STAGES = (
    "manifests",
    "stops",
    "verifications",
    "payments",
    "photos",
    "failed_attempts",
    "preferences",
    "reports",
    "ratings",
    "outbox",
    "inbox",
)

_CLOSED_VALUES = tuple(status.value for status in CLOSED_STATUSES)


class SqlAlchemyDeliveryUnitOfWork:
    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session: SaSession | None = None
        self._pending: dict[str, dict] | None = None
        self._manifests = _ManifestRepo(self)
        self._stops = _StopRepo(self)
        self._verifications = _VerificationRepo(self)
        self._payments = _PaymentRepo(self)
        self._photos = _PhotoRepo(self)
        self._failed_attempts = _FailedAttemptRepo(self)
        self._receiver = _ReceiverRepo(self)
        self._ratings = _RatingRepo(self)
        self._outbox = _OutboxRepo(self)
        self._inbox = _InboxRepo(self)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {name: {} for name in _STAGES}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            self._flush(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
            self._pending = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending = None

    def _flush(self, session: SaSession) -> None:
        pending = self._pending
        assert pending is not None
        for entity, previous in pending["manifests"].values():
            _versioned(
                session,
                DeliveryManifestRow,
                DeliveryManifestRow.manifest_id == entity.manifest_id,
                previous=previous,
                values=_manifest_values(entity),
                new_row=lambda e=entity: DeliveryManifestRow(
                    manifest_id=e.manifest_id, **_manifest_values(e)
                ),
            )
        for entity, previous in pending["stops"].values():
            _versioned(
                session,
                DeliveryStopRow,
                DeliveryStopRow.stop_id == entity.stop_id,
                previous=previous,
                values=_stop_values(entity),
                new_row=lambda e=entity: DeliveryStopRow(
                    stop_id=e.stop_id, **_stop_values(e), created_at=_created_at(e)
                ),
            )
        for entity in pending["verifications"].values():
            session.add(VerificationAttemptRow(**_verification_values(entity)))
        for entity in pending["payments"].values():
            session.add(PaymentRecordRow(**_payment_values(entity)))
        for entity in pending["photos"].values():
            session.add(PhotoEvidenceRow(**_photo_values(entity)))
        for entity, previous in pending["failed_attempts"].values():
            _versioned(
                session,
                FailedAttemptRow,
                FailedAttemptRow.attempt_id == entity.attempt_id,
                previous=previous,
                values=_failed_attempt_values(entity),
                new_row=lambda e=entity: FailedAttemptRow(
                    attempt_id=e.attempt_id, **_failed_attempt_values(e)
                ),
            )
        for entity, previous in pending["preferences"].values():
            _versioned(
                session,
                ReceiverPreferenceRow,
                ReceiverPreferenceRow.preference_id == entity.preference_id,
                previous=previous,
                values=_preference_values(entity),
                new_row=lambda e=entity: ReceiverPreferenceRow(
                    preference_id=e.preference_id, **_preference_values(e)
                ),
            )
        for entity in pending["reports"].values():
            session.add(ParcelIssueReportRow(**_report_values(entity)))
        for entity, previous in pending["ratings"].values():
            _versioned(
                session,
                CourierRatingRow,
                CourierRatingRow.rating_id == entity.rating_id,
                previous=previous,
                values=_rating_values(entity),
                new_row=lambda e=entity: CourierRatingRow(
                    rating_id=e.rating_id, **_rating_values(e)
                ),
            )
        for record in pending["outbox"].values():
            session.add(IntegrationOutboxRow(**_outbox_values(record)))
        for record, is_new in pending["inbox"].values():
            if is_new:
                session.add(IntegrationInboxRow(**_inbox_values(record)))
            else:
                session.execute(
                    update(IntegrationInboxRow)
                    .where(
                        IntegrationInboxRow.consumer_name == record.consumer_name,
                        IntegrationInboxRow.event_id == record.event_id,
                    )
                    .values(**_inbox_update_values(record))
                )

    def _require(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

    @property
    def manifests(self) -> _ManifestRepo:
        return self._manifests

    @property
    def stops(self) -> _StopRepo:
        return self._stops

    @property
    def verifications(self) -> _VerificationRepo:
        return self._verifications

    @property
    def payments(self) -> _PaymentRepo:
        return self._payments

    @property
    def photos(self) -> _PhotoRepo:
        return self._photos

    @property
    def failed_attempts(self) -> _FailedAttemptRepo:
        return self._failed_attempts

    @property
    def receiver(self) -> _ReceiverRepo:
        return self._receiver

    @property
    def ratings(self) -> _RatingRepo:
        return self._ratings

    @property
    def outbox(self) -> _OutboxRepo:
        return self._outbox

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleDeliveryRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _Repo:
    def __init__(self, uow: SqlAlchemyDeliveryUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _ManifestRepo(_Repo):
    def save(self, manifest: DeliveryManifest) -> None:
        _stage(
            self._stage_for("manifests"),
            manifest.manifest_id,
            manifest,
            manifest.version,
        )

    def get(self, manifest_id: UUID) -> DeliveryManifest | None:
        staged = self._stage_for("manifests").get(manifest_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DeliveryManifestRow, manifest_id)
        return _manifest_entity(row) if row is not None else None

    def find_open_for_driver(
        self, driver_principal_id: UUID
    ) -> DeliveryManifest | None:
        for staged, _ in self._stage_for("manifests").values():
            if staged.driver_principal_id == driver_principal_id and staged.is_open:
                return staged
        row = (
            self._session()
            .execute(
                select(DeliveryManifestRow).where(
                    DeliveryManifestRow.driver_principal_id == driver_principal_id,
                    DeliveryManifestRow.closed_at.is_(None),
                )
            )
            .scalars()
            .first()
        )
        return _manifest_entity(row) if row is not None else None


class _StopRepo(_Repo):
    def save(self, stop: DeliveryStop) -> None:
        _stage(self._stage_for("stops"), stop.stop_id, stop, stop.version)

    def get(self, stop_id: UUID) -> DeliveryStop | None:
        staged = self._stage_for("stops").get(stop_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DeliveryStopRow, stop_id)
        return _stop_entity(row) if row is not None else None

    def find_by_tracking_code(self, tracking_code: str) -> DeliveryStop | None:
        for staged, _ in self._stage_for("stops").values():
            if staged.tracking_code == tracking_code:
                return staged
        row = (
            self._session()
            .execute(
                select(DeliveryStopRow)
                .where(DeliveryStopRow.tracking_code == tracking_code)
                .order_by(DeliveryStopRow.created_at.desc())
            )
            .scalars()
            .first()
        )
        return _stop_entity(row) if row is not None else None

    def find_live_by_tracking_code(self, tracking_code: str) -> DeliveryStop | None:
        for staged, _ in self._stage_for("stops").values():
            if (
                staged.tracking_code == tracking_code
                and staged.status not in CLOSED_STATUSES
            ):
                return staged
        row = (
            self._session()
            .execute(
                select(DeliveryStopRow).where(
                    DeliveryStopRow.tracking_code == tracking_code,
                    DeliveryStopRow.status.not_in(_CLOSED_VALUES),
                )
            )
            .scalars()
            .first()
        )
        return _stop_entity(row) if row is not None else None

    def list_for_manifest(self, manifest_id: UUID) -> tuple[DeliveryStop, ...]:
        rows = (
            self._session()
            .execute(
                select(DeliveryStopRow)
                .where(DeliveryStopRow.manifest_id == manifest_id)
                .order_by(DeliveryStopRow.created_at)
            )
            .scalars()
            .all()
        )
        return tuple(_stop_entity(row) for row in rows)

    def list_for_driver(self, driver_principal_id: UUID) -> tuple[DeliveryStop, ...]:
        rows = (
            self._session()
            .execute(
                select(DeliveryStopRow)
                .where(DeliveryStopRow.driver_principal_id == driver_principal_id)
                .order_by(DeliveryStopRow.created_at)
            )
            .scalars()
            .all()
        )
        return tuple(_stop_entity(row) for row in rows)


class _VerificationRepo(_Repo):
    def save(self, attempt: VerificationAttempt) -> None:
        self._stage_for("verifications")[attempt.attempt_id] = attempt

    def list_for_stop(self, stop_id: UUID) -> tuple[VerificationAttempt, ...]:
        staged = tuple(
            a for a in self._stage_for("verifications").values() if a.stop_id == stop_id
        )
        rows = (
            self._session()
            .execute(
                select(VerificationAttemptRow)
                .where(VerificationAttemptRow.stop_id == stop_id)
                .order_by(VerificationAttemptRow.attempted_at)
            )
            .scalars()
            .all()
        )
        return tuple(_verification_entity(row) for row in rows) + staged


class _PaymentRepo(_Repo):
    def save(self, payment: PaymentRecord) -> None:
        self._stage_for("payments")[payment.payment_id] = payment

    def find_for_stop(self, stop_id: UUID) -> PaymentRecord | None:
        for staged in self._stage_for("payments").values():
            if staged.stop_id == stop_id:
                return staged
        row = (
            self._session()
            .execute(select(PaymentRecordRow).where(PaymentRecordRow.stop_id == stop_id))
            .scalars()
            .first()
        )
        return _payment_entity(row) if row is not None else None


class _PhotoRepo(_Repo):
    def save(self, photo: PhotoEvidence) -> None:
        self._stage_for("photos")[photo.photo_id] = photo

    def list_for_stop(self, stop_id: UUID) -> tuple[PhotoEvidence, ...]:
        staged = tuple(
            p for p in self._stage_for("photos").values() if p.stop_id == stop_id
        )
        rows = (
            self._session()
            .execute(
                select(PhotoEvidenceRow)
                .where(PhotoEvidenceRow.stop_id == stop_id)
                .order_by(PhotoEvidenceRow.captured_at)
            )
            .scalars()
            .all()
        )
        return tuple(_photo_entity(row) for row in rows) + staged


class _FailedAttemptRepo(_Repo):
    def save(self, attempt: FailedAttempt) -> None:
        _stage(
            self._stage_for("failed_attempts"),
            attempt.attempt_id,
            attempt,
            attempt.version,
        )

    def get(self, attempt_id: UUID) -> FailedAttempt | None:
        staged = self._stage_for("failed_attempts").get(attempt_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(FailedAttemptRow, attempt_id)
        return _failed_attempt_entity(row) if row is not None else None

    def list_awaiting_operations(self) -> tuple[FailedAttempt, ...]:
        rows = (
            self._session()
            .execute(
                select(FailedAttemptRow)
                .where(FailedAttemptRow.next_attempt_decision.is_(None))
                .order_by(FailedAttemptRow.recorded_at)
            )
            .scalars()
            .all()
        )
        return tuple(_failed_attempt_entity(row) for row in rows)

    def list_for_stop(self, stop_id: UUID) -> tuple[FailedAttempt, ...]:
        rows = (
            self._session()
            .execute(
                select(FailedAttemptRow)
                .where(FailedAttemptRow.stop_id == stop_id)
                .order_by(FailedAttemptRow.recorded_at)
            )
            .scalars()
            .all()
        )
        return tuple(_failed_attempt_entity(row) for row in rows)


class _ReceiverRepo(_Repo):
    def save_preference(self, preference: ReceiverPreference) -> None:
        _stage(
            self._stage_for("preferences"),
            preference.preference_id,
            preference,
            preference.version,
        )

    def find_preference(self, tracking_code: str) -> ReceiverPreference | None:
        for staged, _ in self._stage_for("preferences").values():
            if staged.tracking_code == tracking_code:
                return staged
        row = (
            self._session()
            .execute(
                select(ReceiverPreferenceRow).where(
                    ReceiverPreferenceRow.tracking_code == tracking_code
                )
            )
            .scalars()
            .first()
        )
        return _preference_entity(row) if row is not None else None

    def save_report(self, report: ParcelIssueReport) -> None:
        self._stage_for("reports")[report.report_id] = report

    def list_reports(self, tracking_code: str) -> tuple[ParcelIssueReport, ...]:
        staged = tuple(
            r
            for r in self._stage_for("reports").values()
            if r.tracking_code == tracking_code
        )
        rows = (
            self._session()
            .execute(
                select(ParcelIssueReportRow)
                .where(ParcelIssueReportRow.tracking_code == tracking_code)
                .order_by(ParcelIssueReportRow.reported_at)
            )
            .scalars()
            .all()
        )
        return tuple(_report_entity(row) for row in rows) + staged


class _RatingRepo(_Repo):
    def save(self, rating: CourierRating) -> None:
        _stage(self._stage_for("ratings"), rating.rating_id, rating, rating.version)

    def find_for_tracking_code(self, tracking_code: str) -> CourierRating | None:
        for staged, _ in self._stage_for("ratings").values():
            if staged.tracking_code == tracking_code:
                return staged
        row = (
            self._session()
            .execute(
                select(CourierRatingRow).where(
                    CourierRatingRow.tracking_code == tracking_code
                )
            )
            .scalars()
            .first()
        )
        return _rating_entity(row) if row is not None else None

    def list_for_courier(self, courier_principal_id: UUID) -> tuple[CourierRating, ...]:
        rows = (
            self._session()
            .execute(
                select(CourierRatingRow)
                .where(CourierRatingRow.courier_principal_id == courier_principal_id)
                .order_by(CourierRatingRow.rated_at)
            )
            .scalars()
            .all()
        )
        return tuple(_rating_entity(row) for row in rows)


class _OutboxRepo(_Repo):
    def insert(self, record: OutboxRecord) -> None:
        self._stage_for("outbox")[record.id] = record

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        for staged in self._stage_for("outbox").values():
            if staged.event_id == event_id:
                return staged
        row = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.event_id == event_id
                )
            )
            .scalars()
            .first()
        )
        return _outbox_entity(row) if row is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.status == OutboxStatus.PENDING.value)
                .order_by(IntegrationOutboxRow.created_at)
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.aggregate_id == aggregate_id)
                .order_by(IntegrationOutboxRow.aggregate_version)
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        staged = self._stage_for("inbox").get((consumer_name, event_id))
        if staged is not None:
            return staged[0]
        row = (
            self._session()
            .execute(
                select(IntegrationInboxRow).where(
                    IntegrationInboxRow.consumer_name == consumer_name,
                    IntegrationInboxRow.event_id == event_id,
                )
            )
            .scalars()
            .first()
        )
        return _inbox_entity(row) if row is not None else None

    def insert(self, record: InboxRecord) -> None:
        self._stage_for("inbox")[(record.consumer_name, record.event_id)] = (
            record,
            True,
        )

    def save(self, record: InboxRecord) -> None:
        key = (record.consumer_name, record.event_id)
        staged = self._stage_for("inbox").get(key)
        is_new = staged[1] if staged is not None else False
        self._stage_for("inbox")[key] = (record, is_new)


# ------------------------------------------------------------------ mapping


def _created_at(stop: DeliveryStop):
    """The moment the parcel was scanned onto a manifest.

    ``created_at`` is what orders a parcel's successive stops, so it is never null: a
    stop written before custody was taken falls back to now rather than to nothing.
    """
    return stop.custody_taken_at or datetime.now(tz=UTC)


def _money_columns(amount: Money | None, prefix: str) -> dict:
    if amount is None:
        return {f"{prefix}_minor_units": None, f"{prefix}_currency": None}
    return {
        f"{prefix}_minor_units": amount.minor_units,
        f"{prefix}_currency": amount.currency.value,
    }


def _money(minor_units, currency) -> Money | None:
    if minor_units is None or currency is None:
        return None
    return Money(minor_units=int(minor_units), currency=Currency(currency))


def _media_columns(media: EvidenceMediaRef | None, prefix: str) -> dict:
    if media is None:
        return {
            f"{prefix}_bucket": None,
            f"{prefix}_key": None,
            f"{prefix}_content_type": None,
        }
    return {
        f"{prefix}_bucket": media.bucket,
        f"{prefix}_key": media.key,
        f"{prefix}_content_type": media.content_type,
    }


def _media(bucket, key, content_type) -> EvidenceMediaRef | None:
    if bucket is None or key is None:
        return None
    return EvidenceMediaRef(bucket=bucket, key=key, content_type=content_type)


def _manifest_values(entity: DeliveryManifest) -> dict:
    return {
        "driver_principal_id": entity.driver_principal_id,
        "hub_id": entity.hub_id,
        "created_at": entity.created_at,
        "closed_at": entity.closed_at,
        "version": entity.version,
    }


def _manifest_entity(row: DeliveryManifestRow) -> DeliveryManifest:
    return DeliveryManifest(
        manifest_id=row.manifest_id,
        driver_principal_id=row.driver_principal_id,
        hub_id=row.hub_id,
        created_at=row.created_at,
        closed_at=row.closed_at,
        version=row.version,
    )


def _stop_values(entity: DeliveryStop) -> dict:
    return {
        "manifest_id": entity.manifest_id,
        "tracking_code": entity.tracking_code,
        "driver_principal_id": entity.driver_principal_id,
        "status": entity.status.value,
        "open_box_allowed": entity.settings.open_box_allowed,
        "photo_documentation": entity.settings.photo_documentation,
        "packaging_seal_code": entity.settings.packaging_seal_code,
        "delivery_code_digest": entity.delivery_code_digest,
        "named_receiver": entity.named_receiver,
        **_money_columns(entity.cod_amount, "cod_amount"),
        "payment_method_expected": entity.payment_method_expected.value,
        "custody_taken_at": entity.custody_taken_at,
        "departed_at": entity.departed_at,
        "arrived_at": entity.arrived_at,
        "wait_started_at": entity.wait_started_at,
        "verified_at": entity.verified_at,
        "verified_by_method": _value(entity.verified_by_method),
        "seal_outcome": _value(entity.seal_outcome),
        "inspection_outcome": _value(entity.inspection_outcome),
        "delivered_at": entity.delivered_at,
        "failure_reason": _value(entity.failure_reason),
        "refusal_reason": _value(entity.refusal_reason),
        "closed_at": entity.closed_at,
        "code_attempt_count": entity.code_attempt_count,
        "version": entity.version,
    }


def _stop_entity(row: DeliveryStopRow) -> DeliveryStop:
    return DeliveryStop(
        stop_id=row.stop_id,
        manifest_id=row.manifest_id,
        tracking_code=row.tracking_code,
        driver_principal_id=row.driver_principal_id,
        status=StopStatus(row.status),
        settings=ParcelSettings(
            open_box_allowed=row.open_box_allowed,
            photo_documentation=row.photo_documentation,
            packaging_seal_code=row.packaging_seal_code,
        ),
        delivery_code_digest=row.delivery_code_digest,
        named_receiver=row.named_receiver,
        cod_amount=_money(row.cod_amount_minor_units, row.cod_amount_currency),
        payment_method_expected=PaymentMethod(row.payment_method_expected),
        custody_taken_at=row.custody_taken_at,
        departed_at=row.departed_at,
        arrived_at=row.arrived_at,
        wait_started_at=row.wait_started_at,
        verified_at=row.verified_at,
        verified_by_method=_enum(VerificationMethod, row.verified_by_method),
        seal_outcome=_enum(SealCheckOutcome, row.seal_outcome),
        inspection_outcome=_enum(InspectionOutcome, row.inspection_outcome),
        delivered_at=row.delivered_at,
        failure_reason=_enum(FailureReason, row.failure_reason),
        refusal_reason=_enum(RefusalReason, row.refusal_reason),
        closed_at=row.closed_at,
        code_attempt_count=row.code_attempt_count,
        version=row.version,
    )


def _verification_values(entity: VerificationAttempt) -> dict:
    return {
        "attempt_id": entity.attempt_id,
        "stop_id": entity.stop_id,
        "method": entity.method.value,
        "outcome": entity.outcome.value,
        "attempted_at": entity.attempted_at,
        "attempted_by_actor_id": entity.attempted_by_actor_id,
        "id_matched_named_receiver": entity.id_matched_named_receiver,
    }


def _verification_entity(row: VerificationAttemptRow) -> VerificationAttempt:
    return VerificationAttempt(
        attempt_id=row.attempt_id,
        stop_id=row.stop_id,
        method=VerificationMethod(row.method),
        outcome=VerificationOutcome(row.outcome),
        attempted_at=row.attempted_at,
        attempted_by_actor_id=row.attempted_by_actor_id,
        id_matched_named_receiver=row.id_matched_named_receiver,
    )


def _payment_values(entity: PaymentRecord) -> dict:
    return {
        "payment_id": entity.payment_id,
        "stop_id": entity.stop_id,
        "method": entity.method.value,
        "outcome": entity.outcome.value,
        **_money_columns(entity.amount, "amount"),
        "pos_reference": entity.pos_reference,
        **_media_columns(entity.pos_receipt, "pos_receipt"),
        "recorded_at": entity.recorded_at,
        "recorded_by_actor_id": entity.recorded_by_actor_id,
    }


def _payment_entity(row: PaymentRecordRow) -> PaymentRecord:
    return PaymentRecord(
        payment_id=row.payment_id,
        stop_id=row.stop_id,
        method=PaymentMethod(row.method),
        outcome=PaymentOutcome(row.outcome),
        amount=_money(row.amount_minor_units, row.amount_currency),
        pos_reference=row.pos_reference,
        pos_receipt=_media(
            row.pos_receipt_bucket, row.pos_receipt_key, row.pos_receipt_content_type
        ),
        recorded_at=row.recorded_at,
        recorded_by_actor_id=row.recorded_by_actor_id,
    )


def _photo_values(entity: PhotoEvidence) -> dict:
    return {
        "photo_id": entity.photo_id,
        "stop_id": entity.stop_id,
        "stage": entity.stage.value,
        "media_bucket": entity.media.bucket,
        "media_key": entity.media.key,
        "media_content_type": entity.media.content_type,
        "captured_at": entity.captured_at,
        "captured_by_actor_id": entity.captured_by_actor_id,
    }


def _photo_entity(row: PhotoEvidenceRow) -> PhotoEvidence:
    return PhotoEvidence(
        photo_id=row.photo_id,
        stop_id=row.stop_id,
        stage=PhotoStage(row.stage),
        media=EvidenceMediaRef(
            bucket=row.media_bucket,
            key=row.media_key,
            content_type=row.media_content_type,
        ),
        captured_at=row.captured_at,
        captured_by_actor_id=row.captured_by_actor_id,
    )


def _failed_attempt_values(entity: FailedAttempt) -> dict:
    return {
        "stop_id": entity.stop_id,
        "tracking_code": entity.tracking_code,
        "reason": entity.reason.value,
        "recorded_at": entity.recorded_at,
        "recorded_by_actor_id": entity.recorded_by_actor_id,
        "next_attempt_decision": _value(entity.next_attempt_decision),
        "decided_at": entity.decided_at,
        "decided_by_actor_id": entity.decided_by_actor_id,
        "version": entity.version,
    }


def _failed_attempt_entity(row: FailedAttemptRow) -> FailedAttempt:
    return FailedAttempt(
        attempt_id=row.attempt_id,
        stop_id=row.stop_id,
        tracking_code=row.tracking_code,
        reason=FailureReason(row.reason),
        recorded_at=row.recorded_at,
        recorded_by_actor_id=row.recorded_by_actor_id,
        next_attempt_decision=_enum(NextAttemptDecision, row.next_attempt_decision),
        decided_at=row.decided_at,
        decided_by_actor_id=row.decided_by_actor_id,
        version=row.version,
    )


def _preference_values(entity: ReceiverPreference) -> dict:
    window = entity.window
    geo = entity.geo
    return {
        "tracking_code": entity.tracking_code,
        "window_starts_at_hour": window.starts_at_hour if window else None,
        "window_ends_at_hour": window.ends_at_hour if window else None,
        "address_line": entity.address_line,
        "landmark": entity.landmark,
        "geo_latitude": geo.latitude if geo else None,
        "geo_longitude": geo.longitude if geo else None,
        "set_by_principal_id": entity.set_by_principal_id,
        "updated_at": entity.updated_at,
        "version": entity.version,
    }


def _preference_entity(row: ReceiverPreferenceRow) -> ReceiverPreference:
    window = (
        TimeWindow(
            starts_at_hour=row.window_starts_at_hour,
            ends_at_hour=row.window_ends_at_hour,
        )
        if row.window_starts_at_hour is not None
        else None
    )
    geo = (
        GeoPoint(
            latitude=Decimal(str(row.geo_latitude)),
            longitude=Decimal(str(row.geo_longitude)),
        )
        if row.geo_latitude is not None
        else None
    )
    return ReceiverPreference(
        preference_id=row.preference_id,
        tracking_code=row.tracking_code,
        window=window,
        address_line=row.address_line,
        landmark=row.landmark,
        geo=geo,
        set_by_principal_id=row.set_by_principal_id,
        updated_at=row.updated_at,
        version=row.version,
    )


def _report_values(entity: ParcelIssueReport) -> dict:
    return {
        "report_id": entity.report_id,
        "tracking_code": entity.tracking_code,
        "kind": entity.kind.value,
        "source": entity.source.value,
        "stop_id": entity.stop_id,
        "detail": entity.detail,
        "reported_by_principal_id": entity.reported_by_principal_id,
        "media_json": [
            {"bucket": m.bucket, "key": m.key, "content_type": m.content_type}
            for m in entity.media
        ],
        "reported_at": entity.reported_at,
        "version": entity.version,
    }


def _report_entity(row: ParcelIssueReportRow) -> ParcelIssueReport:
    return ParcelIssueReport(
        report_id=row.report_id,
        tracking_code=row.tracking_code,
        kind=IssueKind(row.kind),
        source=IssueSource(row.source),
        stop_id=row.stop_id,
        detail=row.detail,
        reported_by_principal_id=row.reported_by_principal_id,
        media=tuple(
            EvidenceMediaRef(
                bucket=item["bucket"],
                key=item["key"],
                content_type=item.get("content_type"),
            )
            for item in row.media_json or ()
        ),
        reported_at=row.reported_at,
        version=row.version,
    )


def _rating_values(entity: CourierRating) -> dict:
    return {
        "courier_principal_id": entity.courier_principal_id,
        "tracking_code": entity.tracking_code,
        "score": entity.score,
        "tags": [tag.value for tag in entity.tags],
        "note": entity.note,
        "rated_by_principal_id": entity.rated_by_principal_id,
        "rated_at": entity.rated_at,
        "version": entity.version,
    }


def _rating_entity(row: CourierRatingRow) -> CourierRating:
    return CourierRating(
        rating_id=row.rating_id,
        courier_principal_id=row.courier_principal_id,
        tracking_code=row.tracking_code,
        score=row.score,
        tags=tuple(RatingTag(tag) for tag in row.tags or ()),
        note=row.note,
        rated_by_principal_id=row.rated_by_principal_id,
        rated_at=row.rated_at,
        version=row.version,
    )


def _outbox_values(record: OutboxRecord) -> dict:
    return {
        "id": record.id,
        "event_id": record.event_id,
        "subject": record.subject,
        "event_type": record.event_type,
        "event_version": record.event_version,
        "aggregate_id": record.aggregate_id,
        "aggregate_version": record.aggregate_version,
        "payload_json": record.payload_json,
        "status": record.status.value,
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "next_attempt_at": record.next_attempt_at,
        "processing_owner": record.processing_owner,
        "processing_until": record.processing_until,
        "published_at": record.published_at,
        "last_error_code": record.last_error_code,
        "last_error_message": record.last_error_message,
        "created_at": record.created_at,
    }


def _outbox_entity(row: IntegrationOutboxRow) -> OutboxRecord:
    return OutboxRecord(
        id=row.id,
        event_id=row.event_id,
        subject=row.subject,
        event_type=row.event_type,
        event_version=row.event_version,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        payload_json=row.payload_json,
        status=OutboxStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,
        published_at=row.published_at,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,
    )


def _inbox_values(record: InboxRecord) -> dict:
    return {
        "inbox_id": record.inbox_id,
        "consumer_name": record.consumer_name,
        "event_id": record.event_id,
        "event_type": record.event_type,
        "event_version": record.event_version,
        "status": record.status.value,
        "received_at": record.received_at,
        "attempt_count": record.attempt_count,
        "processing_started_at": record.processing_started_at,
        "processing_lease_until": record.processing_lease_until,
        "processed_at": record.processed_at,
        "last_error_code": record.last_error_code,
        "payload_json": record.payload_json,
    }


def _inbox_update_values(record: InboxRecord) -> dict:
    values = _inbox_values(record)
    # The identity of an inbox row never changes; only its progress does.
    for immutable in ("inbox_id", "consumer_name", "event_id"):
        values.pop(immutable)
    return values


def _inbox_entity(row: IntegrationInboxRow) -> InboxRecord:
    return InboxRecord(
        inbox_id=row.inbox_id,
        consumer_name=row.consumer_name,
        event_id=row.event_id,
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        received_at=row.received_at,
        attempt_count=row.attempt_count,
        processing_started_at=row.processing_started_at,
        processing_lease_until=row.processing_lease_until,
        processed_at=row.processed_at,
        last_error_code=row.last_error_code,
        payload_json=row.payload_json,
    )


def _value(member):
    return member.value if member is not None else None


def _enum(enum_cls, raw):
    return enum_cls(raw) if raw is not None else None
