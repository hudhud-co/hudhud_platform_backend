"""PostgreSQL unit of work for the Claims service.

One instance is one request's transaction. ``begin`` refuses a second on the same
instance, which is what turns the shared-unit-of-work mistake into a loud failure
instead of two requests quietly sharing a session.

Writes are staged during the transaction and flushed on commit with a version-conditional
UPDATE, so two operators deciding the same claim at once produce a
:class:`StaleClaimsRecord` rather than one silently overwriting the other's decision.
Nothing here reads or writes another service's tables.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from claims.domain.entities import ClaimMessage, CompensationClaim, DriverIncident
from claims.domain.errors import StaleClaimsRecord
from claims.domain.messaging import (
    InboxRecord,
    InboxStatus,
    OutboxRecord,
    OutboxStatus,
)
from claims.domain.money import Currency, Money
from claims.domain.value_objects import (
    OPEN_STATUSES,
    ClaimKind,
    ClaimOpenedBy,
    ClaimStatus,
    ConversationAuthor,
    CustodyBoundary,
    EvidenceMediaRef,
    IncidentKind,
    IncidentStatus,
    RejectionReason,
)
from claims.infrastructure.persistence.models import (
    ClaimMessageRow,
    CompensationClaimRow,
    DriverIncidentRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    ReferenceSequenceRow,
)

_STAGES = ("claims", "messages", "incidents", "outbox", "inbox")

_OPEN_VALUES = tuple(status.value for status in OPEN_STATUSES)


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleClaimsRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


# --------------------------------------------------------------------- mapping


def _media(raw: object) -> EvidenceMediaRef:
    assert isinstance(raw, dict)
    return EvidenceMediaRef(
        bucket=str(raw["bucket"]),
        key=str(raw["key"]),
        content_type=raw.get("content_type"),
    )


def _media_json(refs: tuple[EvidenceMediaRef, ...]) -> list[dict]:
    return [
        {"bucket": ref.bucket, "key": ref.key, "content_type": ref.content_type}
        for ref in refs
    ]


def _claim_values(claim: CompensationClaim) -> dict:
    amount = claim.compensation_amount
    return {
        "claim_id": claim.claim_id,
        "reference": claim.reference,
        "tracking_code": claim.tracking_code,
        "kind": claim.kind.value,
        "opened_by": claim.opened_by.value,
        "opened_by_principal_id": claim.opened_by_principal_id,
        "sender_principal_id": claim.sender_principal_id,
        "merchant_id": claim.merchant_id,
        "description": claim.description,
        "evidence_json": _media_json(claim.evidence),
        "custody_boundary": claim.custody_boundary.value,
        "status": claim.status.value,
        "submitted_at": claim.submitted_at,
        "review_started_at": claim.review_started_at,
        "reviewed_by_actor_id": claim.reviewed_by_actor_id,
        "custody_records_reviewed": claim.custody_records_reviewed,
        "decided_at": claim.decided_at,
        "decided_by_actor_id": claim.decided_by_actor_id,
        "compensation_minor_units": amount.minor_units if amount else None,
        "compensation_currency": amount.currency.value if amount else None,
        "rejection_reason": (
            claim.rejection_reason.value if claim.rejection_reason else None
        ),
        "rejection_note": claim.rejection_note,
        "withdrawn_at": claim.withdrawn_at,
        "version": claim.version,
    }


def _claim_entity(row: CompensationClaimRow) -> CompensationClaim:
    amount = None
    if row.compensation_minor_units is not None:
        amount = Money(
            int(row.compensation_minor_units), Currency(row.compensation_currency)
        )
    return CompensationClaim(
        claim_id=row.claim_id,
        reference=row.reference,
        tracking_code=row.tracking_code,
        kind=ClaimKind(row.kind),
        opened_by=ClaimOpenedBy(row.opened_by),
        opened_by_principal_id=row.opened_by_principal_id,
        sender_principal_id=row.sender_principal_id,
        merchant_id=row.merchant_id,
        description=row.description,
        evidence=tuple(_media(item) for item in row.evidence_json or []),
        custody_boundary=CustodyBoundary(row.custody_boundary),
        status=ClaimStatus(row.status),
        submitted_at=row.submitted_at,
        review_started_at=row.review_started_at,
        reviewed_by_actor_id=row.reviewed_by_actor_id,
        custody_records_reviewed=bool(row.custody_records_reviewed),
        decided_at=row.decided_at,
        decided_by_actor_id=row.decided_by_actor_id,
        compensation_amount=amount,
        rejection_reason=(
            RejectionReason(row.rejection_reason) if row.rejection_reason else None
        ),
        rejection_note=row.rejection_note,
        withdrawn_at=row.withdrawn_at,
        version=int(row.version),
    )


def _message_values(message: ClaimMessage) -> dict:
    return {
        "message_id": message.message_id,
        "claim_id": message.claim_id,
        "author": message.author.value,
        "body": message.body,
        "written_at": message.written_at,
        "author_principal_id": message.author_principal_id,
        "attachments_json": _media_json(message.attachments),
        "version": message.version,
    }


def _message_entity(row: ClaimMessageRow) -> ClaimMessage:
    return ClaimMessage(
        message_id=row.message_id,
        claim_id=row.claim_id,
        author=ConversationAuthor(row.author),
        body=row.body,
        written_at=row.written_at,
        author_principal_id=row.author_principal_id,
        attachments=tuple(_media(item) for item in row.attachments_json or []),
        version=int(row.version),
    )


def _incident_values(incident: DriverIncident) -> dict:
    # SEC-07 — there is no amount in this mapping because there is no amount on the
    # entity and no column on the table. Three absences that have to agree.
    return {
        "incident_id": incident.incident_id,
        "reference": incident.reference,
        "kind": incident.kind.value,
        "reported_by_driver_id": incident.reported_by_driver_id,
        "reported_at": incident.reported_at,
        "tracking_code": incident.tracking_code,
        "note": incident.note,
        "evidence_json": _media_json(incident.evidence),
        "status": incident.status.value,
        "investigation_started_at": incident.investigation_started_at,
        "resolved_at": incident.resolved_at,
        "resolved_by_actor_id": incident.resolved_by_actor_id,
        "resolution_note": incident.resolution_note,
        "linked_claim_id": incident.linked_claim_id,
        "version": incident.version,
    }


def _incident_entity(row: DriverIncidentRow) -> DriverIncident:
    return DriverIncident(
        incident_id=row.incident_id,
        reference=row.reference,
        kind=IncidentKind(row.kind),
        reported_by_driver_id=row.reported_by_driver_id,
        reported_at=row.reported_at,
        tracking_code=row.tracking_code,
        note=row.note,
        evidence=tuple(_media(item) for item in row.evidence_json or []),
        status=IncidentStatus(row.status),
        investigation_started_at=row.investigation_started_at,
        resolved_at=row.resolved_at,
        resolved_by_actor_id=row.resolved_by_actor_id,
        resolution_note=row.resolution_note,
        linked_claim_id=row.linked_claim_id,
        version=int(row.version),
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
        event_version=int(row.event_version),
        aggregate_id=row.aggregate_id,
        aggregate_version=int(row.aggregate_version),
        payload_json=dict(row.payload_json or {}),
        status=OutboxStatus(row.status),
        attempt_count=int(row.attempt_count),
        max_attempts=int(row.max_attempts),
        next_attempt_at=row.next_attempt_at,
        created_at=row.created_at,
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,
        published_at=row.published_at,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
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
    for immutable in ("inbox_id", "consumer_name", "event_id"):
        values.pop(immutable)
    return values


def _inbox_entity(row: IntegrationInboxRow) -> InboxRecord:
    return InboxRecord(
        inbox_id=row.inbox_id,
        consumer_name=row.consumer_name,
        event_id=row.event_id,
        event_type=row.event_type,
        event_version=int(row.event_version),
        status=InboxStatus(row.status),
        received_at=row.received_at,
        attempt_count=int(row.attempt_count),
        processing_started_at=row.processing_started_at,
        processing_lease_until=row.processing_lease_until,
        processed_at=row.processed_at,
        last_error_code=row.last_error_code,
        payload_json=dict(row.payload_json or {}),
    )


# --------------------------------------------------------------------- repos


class _Repo:
    def __init__(self, uow: SqlAlchemyClaimsUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _SequenceMixin:
    #: One series for claims *and* incidents. Both are shown as ``CLM-…`` and an
    #: operator reading the OPS-06 view sees them side by side, so two different things
    #: must never wear the same reference.
    _SCOPE = "reference"

    def next_sequence_for_day(self, day: str) -> int:
        """Allocate the day's next reference number, atomically.

        ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` in one statement: reading the
        current value and adding one would hand two simultaneous filers the same
        reference, and a reference is the only identifier the claimant ever sees.
        """
        session = self._session()  # type: ignore[attr-defined]
        statement = (
            pg_insert(ReferenceSequenceRow)
            .values(scope=self._SCOPE, day=day, last_sequence=1)
            .on_conflict_do_update(
                index_elements=[ReferenceSequenceRow.scope, ReferenceSequenceRow.day],
                set_={"last_sequence": ReferenceSequenceRow.last_sequence + 1},
            )
            .returning(ReferenceSequenceRow.last_sequence)
        )
        return int(session.execute(statement).scalar_one())


class _ClaimRepo(_SequenceMixin, _Repo):
    def save(self, claim: CompensationClaim) -> None:
        _stage(self._stage_for("claims"), claim.claim_id, claim, claim.version)

    def get(self, claim_id: UUID) -> CompensationClaim | None:
        staged = self._stage_for("claims").get(claim_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(CompensationClaimRow, claim_id)
        return _claim_entity(row) if row is not None else None

    def find_by_reference(self, reference: str) -> CompensationClaim | None:
        for staged, _ in self._stage_for("claims").values():
            if staged.reference == reference:
                return staged
        row = (
            self._session()
            .execute(
                select(CompensationClaimRow).where(
                    CompensationClaimRow.reference == reference
                )
            )
            .scalars()
            .first()
        )
        return _claim_entity(row) if row is not None else None

    def find_open_for_parcel(self, tracking_code: str) -> CompensationClaim | None:
        for staged, _ in self._stage_for("claims").values():
            if staged.tracking_code == tracking_code and staged.is_open:
                return staged
        row = (
            self._session()
            .execute(
                select(CompensationClaimRow).where(
                    CompensationClaimRow.tracking_code == tracking_code,
                    CompensationClaimRow.status.in_(_OPEN_VALUES),
                )
            )
            .scalars()
            .first()
        )
        return _claim_entity(row) if row is not None else None

    def list_for_parcel(self, tracking_code: str) -> tuple[CompensationClaim, ...]:
        rows = (
            self._session()
            .execute(
                select(CompensationClaimRow)
                .where(CompensationClaimRow.tracking_code == tracking_code)
                .order_by(CompensationClaimRow.submitted_at)
            )
            .scalars()
            .all()
        )
        return tuple(_claim_entity(row) for row in rows)

    def list_for_principal(
        self, principal_id: UUID
    ) -> tuple[CompensationClaim, ...]:
        """CLM-07 — a claimant's own claims: what they opened, or what pays them."""
        rows = (
            self._session()
            .execute(
                select(CompensationClaimRow)
                .where(
                    (CompensationClaimRow.opened_by_principal_id == principal_id)
                    | (CompensationClaimRow.sender_principal_id == principal_id)
                )
                .order_by(CompensationClaimRow.submitted_at.desc())
            )
            .scalars()
            .all()
        )
        return tuple(_claim_entity(row) for row in rows)

    def list_for_operations_view(
        self, limit: int = 200
    ) -> tuple[CompensationClaim, ...]:
        """OPS-06 — open *and* decided, newest first.

        The queue at `list_awaiting_operations` is the work still to do; this view is
        what happened, and an operator who can never see a decided claim can never
        answer "what did we pay on this one?".
        """
        rows = (
            self._session()
            .execute(
                select(CompensationClaimRow)
                .order_by(CompensationClaimRow.submitted_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return tuple(_claim_entity(row) for row in rows)

    def list_awaiting_operations(self) -> tuple[CompensationClaim, ...]:
        rows = (
            self._session()
            .execute(
                select(CompensationClaimRow)
                .where(CompensationClaimRow.status.in_(_OPEN_VALUES))
                .order_by(CompensationClaimRow.submitted_at)
            )
            .scalars()
            .all()
        )
        return tuple(_claim_entity(row) for row in rows)


class _MessageRepo(_Repo):
    def save(self, message: ClaimMessage) -> None:
        self._stage_for("messages")[message.message_id] = message

    def list_for_claim(self, claim_id: UUID) -> tuple[ClaimMessage, ...]:
        staged = [
            message
            for message in self._stage_for("messages").values()
            if message.claim_id == claim_id
        ]
        rows = (
            self._session()
            .execute(
                select(ClaimMessageRow)
                .where(ClaimMessageRow.claim_id == claim_id)
                .order_by(ClaimMessageRow.written_at)
            )
            .scalars()
            .all()
        )
        found = [_message_entity(row) for row in rows] + staged
        return tuple(sorted(found, key=lambda message: message.written_at))

    def count_for_claim(self, claim_id: UUID) -> int:
        return len(self.list_for_claim(claim_id))


class _IncidentRepo(_SequenceMixin, _Repo):
    def save(self, incident: DriverIncident) -> None:
        _stage(
            self._stage_for("incidents"),
            incident.incident_id,
            incident,
            incident.version,
        )

    def get(self, incident_id: UUID) -> DriverIncident | None:
        staged = self._stage_for("incidents").get(incident_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DriverIncidentRow, incident_id)
        return _incident_entity(row) if row is not None else None

    def find_by_reference(self, reference: str) -> DriverIncident | None:
        for staged, _ in self._stage_for("incidents").values():
            if staged.reference == reference:
                return staged
        row = (
            self._session()
            .execute(
                select(DriverIncidentRow).where(
                    DriverIncidentRow.reference == reference
                )
            )
            .scalars()
            .first()
        )
        return _incident_entity(row) if row is not None else None

    def list_for_driver(self, driver_id: UUID) -> tuple[DriverIncident, ...]:
        rows = (
            self._session()
            .execute(
                select(DriverIncidentRow)
                .where(DriverIncidentRow.reported_by_driver_id == driver_id)
                .order_by(DriverIncidentRow.reported_at.desc())
            )
            .scalars()
            .all()
        )
        return tuple(_incident_entity(row) for row in rows)

    def list_open(self) -> tuple[DriverIncident, ...]:
        rows = (
            self._session()
            .execute(
                select(DriverIncidentRow)
                .where(DriverIncidentRow.status != IncidentStatus.RESOLVED.value)
                .order_by(DriverIncidentRow.reported_at)
            )
            .scalars()
            .all()
        )
        return tuple(_incident_entity(row) for row in rows)


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
        pending = self._stage_for("inbox")
        is_new = pending[key][1] if key in pending else False
        pending[key] = (record, is_new)


# --------------------------------------------------------------------- uow


class SqlAlchemyClaimsUnitOfWork:
    """One request's transaction. Never stored on ``app.state``."""

    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session: SaSession | None = None
        self._pending: dict[str, dict] | None = None
        self._claims = _ClaimRepo(self)
        self._messages = _MessageRepo(self)
        self._incidents = _IncidentRepo(self)
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
        for entity, previous in pending["claims"].values():
            _versioned(
                session,
                CompensationClaimRow,
                CompensationClaimRow.claim_id == entity.claim_id,
                previous=previous,
                values=_claim_values(entity),
                new_row=lambda entity=entity: CompensationClaimRow(
                    **_claim_values(entity)
                ),
            )
        for message in pending["messages"].values():
            session.execute(insert(ClaimMessageRow).values(**_message_values(message)))
        for entity, previous in pending["incidents"].values():
            _versioned(
                session,
                DriverIncidentRow,
                DriverIncidentRow.incident_id == entity.incident_id,
                previous=previous,
                values=_incident_values(entity),
                new_row=lambda entity=entity: DriverIncidentRow(
                    **_incident_values(entity)
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
    def claims(self) -> _ClaimRepo:
        return self._claims

    @property
    def messages(self) -> _MessageRepo:
        return self._messages

    @property
    def incidents(self) -> _IncidentRepo:
        return self._incidents

    @property
    def outbox(self) -> _OutboxRepo:
        return self._outbox

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox
