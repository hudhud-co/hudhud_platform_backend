"""In-memory adapters.

These exist so the domain and the application services can be exercised without a
database. They copy nothing and store the objects themselves, which is fine for a test
double but is the reason they are never wired into ``main``.
"""

from __future__ import annotations

from uuid import UUID

from delivery.domain.entities import (
    CourierRating,
    DeliveryManifest,
    DeliveryStop,
    FailedAttempt,
    ParcelIssueReport,
    PaymentRecord,
    PhotoEvidence,
    ReceiverPreference,
    VerificationAttempt,
)
from delivery.domain.messaging import InboxRecord, OutboxRecord, OutboxStatus
from delivery.domain.value_objects import CLOSED_STATUSES


class InMemoryManifestRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, DeliveryManifest] = {}

    def save(self, manifest: DeliveryManifest) -> None:
        self._by_id[manifest.manifest_id] = manifest

    def get(self, manifest_id: UUID) -> DeliveryManifest | None:
        return self._by_id.get(manifest_id)

    def find_open_for_driver(
        self, driver_principal_id: UUID
    ) -> DeliveryManifest | None:
        for manifest in self._by_id.values():
            if manifest.driver_principal_id == driver_principal_id and manifest.is_open:
                return manifest
        return None


class InMemoryStopRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, DeliveryStop] = {}

    def save(self, stop: DeliveryStop) -> None:
        self._by_id[stop.stop_id] = stop

    def get(self, stop_id: UUID) -> DeliveryStop | None:
        return self._by_id.get(stop_id)

    def find_by_tracking_code(self, tracking_code: str) -> DeliveryStop | None:
        # A parcel can be attempted more than once, so the latest stop wins. Insertion
        # order stands in for the ``created_at`` ordering the SQL store uses.
        newest = None
        for stop in self._by_id.values():
            if stop.tracking_code == tracking_code:
                newest = stop
        return newest

    def find_live_by_tracking_code(self, tracking_code: str) -> DeliveryStop | None:
        for stop in self._by_id.values():
            if stop.tracking_code == tracking_code and stop.status not in CLOSED_STATUSES:
                return stop
        return None

    def list_for_manifest(self, manifest_id: UUID) -> tuple[DeliveryStop, ...]:
        return tuple(s for s in self._by_id.values() if s.manifest_id == manifest_id)

    def list_for_driver(self, driver_principal_id: UUID) -> tuple[DeliveryStop, ...]:
        return tuple(
            s
            for s in self._by_id.values()
            if s.driver_principal_id == driver_principal_id
        )


class InMemoryVerificationRepository:
    def __init__(self) -> None:
        self._records: list[VerificationAttempt] = []

    def save(self, attempt: VerificationAttempt) -> None:
        self._records.append(attempt)

    def list_for_stop(self, stop_id: UUID) -> tuple[VerificationAttempt, ...]:
        return tuple(r for r in self._records if r.stop_id == stop_id)


class InMemoryPaymentRepository:
    def __init__(self) -> None:
        self._by_stop: dict[UUID, PaymentRecord] = {}

    def save(self, payment: PaymentRecord) -> None:
        self._by_stop[payment.stop_id] = payment

    def find_for_stop(self, stop_id: UUID) -> PaymentRecord | None:
        return self._by_stop.get(stop_id)


class InMemoryPhotoRepository:
    def __init__(self) -> None:
        self._records: list[PhotoEvidence] = []

    def save(self, photo: PhotoEvidence) -> None:
        self._records.append(photo)

    def list_for_stop(self, stop_id: UUID) -> tuple[PhotoEvidence, ...]:
        return tuple(r for r in self._records if r.stop_id == stop_id)


class InMemoryFailedAttemptRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, FailedAttempt] = {}

    def save(self, attempt: FailedAttempt) -> None:
        self._by_id[attempt.attempt_id] = attempt

    def get(self, attempt_id: UUID) -> FailedAttempt | None:
        return self._by_id.get(attempt_id)

    def list_awaiting_operations(self) -> tuple[FailedAttempt, ...]:
        return tuple(a for a in self._by_id.values() if a.awaits_operations)

    def list_for_stop(self, stop_id: UUID) -> tuple[FailedAttempt, ...]:
        return tuple(a for a in self._by_id.values() if a.stop_id == stop_id)


class InMemoryReceiverRepository:
    def __init__(self) -> None:
        self._preferences: dict[str, ReceiverPreference] = {}
        self._reports: list[ParcelIssueReport] = []

    def save_preference(self, preference: ReceiverPreference) -> None:
        self._preferences[preference.tracking_code] = preference

    def find_preference(self, tracking_code: str) -> ReceiverPreference | None:
        return self._preferences.get(tracking_code)

    def save_report(self, report: ParcelIssueReport) -> None:
        self._reports.append(report)

    def list_reports(self, tracking_code: str) -> tuple[ParcelIssueReport, ...]:
        return tuple(r for r in self._reports if r.tracking_code == tracking_code)


class InMemoryRatingRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, CourierRating] = {}

    def save(self, rating: CourierRating) -> None:
        self._by_id[rating.rating_id] = rating

    def find_for_tracking_code(self, tracking_code: str) -> CourierRating | None:
        for rating in self._by_id.values():
            if rating.tracking_code == tracking_code:
                return rating
        return None

    def list_for_courier(self, courier_principal_id: UUID) -> tuple[CourierRating, ...]:
        return tuple(
            r
            for r in self._by_id.values()
            if r.courier_principal_id == courier_principal_id
        )


class InMemoryOutboxRepository:
    def __init__(self) -> None:
        self._records: list[OutboxRecord] = []

    def insert(self, record: OutboxRecord) -> None:
        # The real table has a unique index on ``event_id``; the double stands in for it
        # so a duplicate is caught in unit tests, not only against PostgreSQL.
        if any(r.event_id == record.event_id for r in self._records):
            msg = f"duplicate outbox event_id: {record.event_id}"
            raise ValueError(msg)
        if any(
            r.aggregate_id == record.aggregate_id
            and r.aggregate_version == record.aggregate_version
            for r in self._records
        ):
            msg = (
                "duplicate outbox aggregate version: "
                f"{record.aggregate_id}@{record.aggregate_version}"
            )
            raise ValueError(msg)
        self._records.append(record)

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        for record in self._records:
            if record.event_id == event_id:
                return record
        return None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        return tuple(r for r in self._records if r.status is OutboxStatus.PENDING)

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        return tuple(
            sorted(
                (r for r in self._records if r.aggregate_id == aggregate_id),
                key=lambda r: r.aggregate_version,
            )
        )


class InMemoryInboxRepository:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, UUID], InboxRecord] = {}

    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        return self._by_key.get((consumer_name, event_id))

    def insert(self, record: InboxRecord) -> None:
        key = (record.consumer_name, record.event_id)
        if key in self._by_key:
            msg = f"duplicate inbox record: {key}"
            raise ValueError(msg)
        self._by_key[key] = record

    def save(self, record: InboxRecord) -> None:
        self._by_key[(record.consumer_name, record.event_id)] = record


class InMemoryDatabase:
    """The rows. Shared by every request, exactly as a database is.

    Split from the unit of work deliberately. A unit of work is *one request's*
    transaction; the rows outlive it. Holding both in one object is what allowed a single
    instance to be stored on `app.state` and serve every request at once — measured
    against real PostgreSQL, 1 of 40 concurrent requests succeeded and the rest raised
    "transaction already active".
    """

    def __init__(self) -> None:
        self.manifests = InMemoryManifestRepository()
        self.stops = InMemoryStopRepository()
        self.verifications = InMemoryVerificationRepository()
        self.payments = InMemoryPaymentRepository()
        self.photos = InMemoryPhotoRepository()
        self.failed_attempts = InMemoryFailedAttemptRepository()
        self.receiver = InMemoryReceiverRepository()
        self.ratings = InMemoryRatingRepository()
        self.outbox = InMemoryOutboxRepository()
        self.inbox = InMemoryInboxRepository()


class InMemoryUnitOfWork:
    """One request's transaction over a shared :class:`InMemoryDatabase`.

    Rollback does not undo in-memory mutations, and says so: pretending to would make
    this double disagree with SQLAlchemy in a way that hides bugs rather than exposing
    them. Tests that care about rollback run against PostgreSQL.

    What it copies exactly is the transaction discipline — ``begin`` refuses to open a
    second transaction, just as the SQLAlchemy store does. A double more permissive than
    the thing it stands in for is worse than no double.
    """

    def __init__(self, database: InMemoryDatabase | None = None) -> None:
        self._database = database or InMemoryDatabase()
        self._active = False
        self.depth = 0
        self.commits = 0
        self.rollbacks = 0

    @property
    def database(self) -> InMemoryDatabase:
        """The shared rows, so a factory can give the next request the same data."""
        return self._database

    def new_unit_of_work(self) -> InMemoryUnitOfWork:
        """Another transaction over the same rows — one per request."""
        return InMemoryUnitOfWork(self._database)

    @property
    def manifests(self) -> InMemoryManifestRepository:
        return self._database.manifests

    @property
    def stops(self) -> InMemoryStopRepository:
        return self._database.stops

    @property
    def verifications(self) -> InMemoryVerificationRepository:
        return self._database.verifications

    @property
    def payments(self) -> InMemoryPaymentRepository:
        return self._database.payments

    @property
    def photos(self) -> InMemoryPhotoRepository:
        return self._database.photos

    @property
    def failed_attempts(self) -> InMemoryFailedAttemptRepository:
        return self._database.failed_attempts

    @property
    def receiver(self) -> InMemoryReceiverRepository:
        return self._database.receiver

    @property
    def ratings(self) -> InMemoryRatingRepository:
        return self._database.ratings

    @property
    def outbox(self) -> InMemoryOutboxRepository:
        return self._database.outbox

    @property
    def inbox(self) -> InMemoryInboxRepository:
        return self._database.inbox

    def begin(self) -> None:
        if self._active:
            # The same message the SQLAlchemy store raises, so a leaked transaction
            # fails identically in a unit test and in production.
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._active = True
        self.depth += 1

    def commit(self) -> None:
        if not self._active:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._active = False
        self.depth = max(0, self.depth - 1)
        self.commits += 1

    def rollback(self) -> None:
        self._active = False
        self.depth = max(0, self.depth - 1)
        self.rollbacks += 1
