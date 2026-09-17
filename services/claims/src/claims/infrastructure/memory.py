"""In-memory adapters for the Claims service.

Built request-scoped from the start: the rows live in :class:`InMemoryDatabase` and are
shared, and each request gets its own :class:`InMemoryUnitOfWork` over them. The platform
learned this the hard way — a single unit of work on `app.state` meant 1 of 40 concurrent
requests succeeded — and `tests/architecture/test_request_scoped_state.py` now enforces it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from claims.domain.entities import ClaimMessage, CompensationClaim, DriverIncident
from claims.domain.messaging import InboxRecord, OutboxRecord, OutboxStatus
from claims.domain.value_objects import OPEN_STATUSES, IncidentStatus

#: Sort key for a claim that somehow has no submitted time; it sorts oldest.
_EPOCH = datetime.min.replace(tzinfo=UTC)


class ReferenceSeries:
    """One daily counter for claims *and* incidents.

    Both are shown as ``CLM-20260802-000003`` and the OPS-06 view lists them side by
    side, so two different things must never wear the same reference. Separate counters
    per repository is how they would.
    """

    def __init__(self) -> None:
        self._by_day: dict[str, int] = {}

    def next_for_day(self, day: str) -> int:
        self._by_day[day] = self._by_day.get(day, 0) + 1
        return self._by_day[day]


class InMemoryClaimRepository:
    def __init__(self, references: ReferenceSeries | None = None) -> None:
        self._by_id: dict[UUID, CompensationClaim] = {}
        self._references = references or ReferenceSeries()

    def save(self, claim: CompensationClaim) -> None:
        self._by_id[claim.claim_id] = claim

    def get(self, claim_id: UUID) -> CompensationClaim | None:
        return self._by_id.get(claim_id)

    def find_by_reference(self, reference: str) -> CompensationClaim | None:
        for claim in self._by_id.values():
            if claim.reference == reference:
                return claim
        return None

    def find_open_for_parcel(self, tracking_code: str) -> CompensationClaim | None:
        for claim in self._by_id.values():
            if claim.tracking_code == tracking_code and claim.is_open:
                return claim
        return None

    def list_for_parcel(self, tracking_code: str) -> tuple[CompensationClaim, ...]:
        return tuple(
            c for c in self._by_id.values() if c.tracking_code == tracking_code
        )

    def list_for_principal(self, principal_id: UUID) -> tuple[CompensationClaim, ...]:
        return tuple(
            claim
            for claim in self._by_id.values()
            if principal_id
            in {claim.opened_by_principal_id, claim.sender_principal_id}
        )

    def list_awaiting_operations(self) -> tuple[CompensationClaim, ...]:
        return tuple(
            claim for claim in self._by_id.values() if claim.status in OPEN_STATUSES
        )

    def list_for_operations_view(
        self, limit: int = 200
    ) -> tuple[CompensationClaim, ...]:
        ordered = sorted(
            self._by_id.values(),
            key=lambda claim: claim.submitted_at or _EPOCH,
            reverse=True,
        )
        return tuple(ordered[:limit])

    def next_sequence_for_day(self, day: str) -> int:
        return self._references.next_for_day(day)


class InMemoryMessageRepository:
    def __init__(self) -> None:
        self._records: list[ClaimMessage] = []

    def save(self, message: ClaimMessage) -> None:
        self._records.append(message)

    def list_for_claim(self, claim_id: UUID) -> tuple[ClaimMessage, ...]:
        return tuple(m for m in self._records if m.claim_id == claim_id)

    def count_for_claim(self, claim_id: UUID) -> int:
        return sum(1 for m in self._records if m.claim_id == claim_id)


class InMemoryIncidentRepository:
    def __init__(self, references: ReferenceSeries | None = None) -> None:
        self._by_id: dict[UUID, DriverIncident] = {}
        self._references = references or ReferenceSeries()

    def save(self, incident: DriverIncident) -> None:
        self._by_id[incident.incident_id] = incident

    def get(self, incident_id: UUID) -> DriverIncident | None:
        return self._by_id.get(incident_id)

    def find_by_reference(self, reference: str) -> DriverIncident | None:
        for incident in self._by_id.values():
            if incident.reference == reference:
                return incident
        return None

    def list_for_driver(self, driver_id: UUID) -> tuple[DriverIncident, ...]:
        return tuple(
            i for i in self._by_id.values() if i.reported_by_driver_id == driver_id
        )

    def list_open(self) -> tuple[DriverIncident, ...]:
        return tuple(
            i for i in self._by_id.values() if i.status is not IncidentStatus.RESOLVED
        )

    def next_sequence_for_day(self, day: str) -> int:
        return self._references.next_for_day(day)


class InMemoryOutboxRepository:
    def __init__(self) -> None:
        self._records: list[OutboxRecord] = []

    def insert(self, record: OutboxRecord) -> None:
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
    """The rows. Shared by every request, exactly as a database is."""

    def __init__(self) -> None:
        references = ReferenceSeries()
        self.claims = InMemoryClaimRepository(references)
        self.messages = InMemoryMessageRepository()
        self.incidents = InMemoryIncidentRepository(references)
        self.outbox = InMemoryOutboxRepository()
        self.inbox = InMemoryInboxRepository()


class InMemoryUnitOfWork:
    """One request's transaction over a shared :class:`InMemoryDatabase`.

    ``begin`` refuses a second transaction on the same instance, exactly as the
    SQLAlchemy store does. A double that is more permissive than the thing it stands in
    for hides the misuse it exists to catch.
    """

    def __init__(self, database: InMemoryDatabase | None = None) -> None:
        self._database = database or InMemoryDatabase()
        self._active = False
        self.depth = 0
        self.commits = 0
        self.rollbacks = 0

    @property
    def database(self) -> InMemoryDatabase:
        return self._database

    def new_unit_of_work(self) -> InMemoryUnitOfWork:
        """Another transaction over the same rows — one per request."""
        return InMemoryUnitOfWork(self._database)

    @property
    def claims(self) -> InMemoryClaimRepository:
        return self._database.claims

    @property
    def messages(self) -> InMemoryMessageRepository:
        return self._database.messages

    @property
    def incidents(self) -> InMemoryIncidentRepository:
        return self._database.incidents

    @property
    def outbox(self) -> InMemoryOutboxRepository:
        return self._database.outbox

    @property
    def inbox(self) -> InMemoryInboxRepository:
        return self._database.inbox

    def begin(self) -> None:
        if self._active:
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
