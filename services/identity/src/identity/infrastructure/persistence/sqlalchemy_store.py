"""PostgreSQL unit of work for the Identity service.

Writes are buffered until commit and applied with a version-conditional UPDATE, so two
concurrent commands cannot silently overwrite each other's counters — which matters most
for the OTP failed-attempt counter, the whole point of which is to be hard to race.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from identity.domain.entities import OtpChallenge, Principal, RoleGrant, Session
from identity.domain.value_objects import OtpPurpose, PrincipalStatus, Role, ScopeKind
from identity.infrastructure.persistence.models import (
    OtpChallengeRow,
    PrincipalRow,
    RoleGrantRow,
    SessionRow,
)


class StaleIdentityRecord(RuntimeError):
    """A concurrent writer changed the row since it was read."""


class SqlAlchemyIdentityUnitOfWork:
    """One instance serves every request, so its transaction is request-scoped.

    ``create_app`` builds this once and puts it on ``app.state``. Holding the
    open session on ``self`` meant two requests in flight shared it, and the
    second ``begin()`` raised ``transaction already active`` — a 500 for any two
    simultaneous users, observed in the dev stack.

    ``_session`` and ``_pending`` are therefore properties over
    :class:`~contextvars.ContextVar`. FastAPI runs each request in its own
    context (and anyio copies that context into the worker thread for sync
    endpoints), so each request gets its own transaction while every existing
    ``self._session`` call site keeps working unchanged.
    """

    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session_var: ContextVar[SaSession | None] = ContextVar(
            "identity_session", default=None
        )
        self._pending_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "identity_pending", default=None
        )
        self._principals = _PrincipalRepo(self)
        self._otp = _OtpRepo(self)
        self._sessions = _SessionRepo(self)
        self._grants = _GrantRepo(self)

    # --------------------------------------------------- request-scoped state

    @property
    def _session(self) -> SaSession | None:
        return self._session_var.get()

    @_session.setter
    def _session(self, value: SaSession | None) -> None:
        self._session_var.set(value)

    @property
    def _pending(self) -> dict[str, dict] | None:
        return self._pending_var.get()

    @_pending.setter
    def _pending(self, value: dict[str, dict] | None) -> None:
        self._pending_var.set(value)

    # ------------------------------------------------------------- transaction

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {"principals": {}, "otp": {}, "sessions": {}, "grants": {}}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            for entity, previous in self._pending["principals"].values():
                _upsert_versioned(
                    session,
                    PrincipalRow,
                    PrincipalRow.principal_id,
                    entity.principal_id,
                    previous=previous,
                    values=_principal_values(entity),
                    new_row=_principal_row(entity),
                )
            for entity, previous in self._pending["otp"].values():
                _upsert_versioned(
                    session,
                    OtpChallengeRow,
                    OtpChallengeRow.challenge_id,
                    entity.challenge_id,
                    previous=previous,
                    values=_otp_values(entity),
                    new_row=_otp_row(entity),
                )
            for entity, previous in self._pending["sessions"].values():
                _upsert_versioned(
                    session,
                    SessionRow,
                    SessionRow.session_id,
                    entity.session_id,
                    previous=previous,
                    values=_session_values(entity),
                    new_row=_session_row(entity),
                )
            for entity, existed in self._pending["grants"].values():
                if existed:
                    session.execute(
                        update(RoleGrantRow)
                        .where(RoleGrantRow.grant_id == entity.grant_id)
                        .values(**_grant_values(entity))
                    )
                else:
                    session.add(_grant_row(entity))
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

    def _require_session(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

    # ------------------------------------------------------------ repositories

    @property
    def principals(self) -> _PrincipalRepo:
        return self._principals

    @property
    def otp_challenges(self) -> _OtpRepo:
        return self._otp

    @property
    def sessions(self) -> _SessionRepo:
        return self._sessions

    @property
    def role_grants(self) -> _GrantRepo:
        return self._grants


def _upsert_versioned(
    session, row_cls, pk_column, pk_value, *, previous, values, new_row
):
    if previous is None:
        session.add(new_row)
        return
    rowcount = session.execute(
        update(row_cls)
        .where(pk_column == pk_value, row_cls.version == previous)
        .values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleIdentityRecord(f"{row_cls.__tablename__} {pk_value} changed concurrently")


class _PrincipalRepo:
    def __init__(self, uow: SqlAlchemyIdentityUnitOfWork) -> None:
        self._uow = uow

    def save(self, principal: Principal) -> None:
        pending = self._uow._pending["principals"]
        previous = pending[principal.principal_id][1] if principal.principal_id in pending else None
        if previous is None and principal.version > 1:
            previous = principal.version - 1
        pending[principal.principal_id] = (principal, previous)

    def get(self, principal_id: UUID) -> Principal | None:
        row = self._uow._require_session().get(PrincipalRow, principal_id)
        return _principal_entity(row) if row is not None else None

    def find_by_phone_hash(self, phone_hash: str) -> Principal | None:
        row = self._uow._require_session().execute(
            select(PrincipalRow).where(PrincipalRow.phone_hash == phone_hash)
        ).scalar_one_or_none()
        return _principal_entity(row) if row is not None else None


class _OtpRepo:
    def __init__(self, uow: SqlAlchemyIdentityUnitOfWork) -> None:
        self._uow = uow

    def save(self, challenge: OtpChallenge) -> None:
        pending = self._uow._pending["otp"]
        previous = pending[challenge.challenge_id][1] if challenge.challenge_id in pending else None
        if previous is None and challenge.version > 1:
            previous = challenge.version - 1
        pending[challenge.challenge_id] = (challenge, previous)

    def get(self, challenge_id: UUID) -> OtpChallenge | None:
        row = self._uow._require_session().get(OtpChallengeRow, challenge_id)
        return _otp_entity(row) if row is not None else None

    def count_issued_since(self, phone_hash: str, since: datetime) -> int:
        rows = self._uow._require_session().execute(
            select(OtpChallengeRow.challenge_id).where(
                OtpChallengeRow.phone_hash == phone_hash,
                OtpChallengeRow.issued_at >= since,
            )
        ).all()
        return len(rows)

    def list_open_for_phone(self, phone_hash: str) -> tuple[OtpChallenge, ...]:
        rows = self._uow._require_session().execute(
            select(OtpChallengeRow).where(
                OtpChallengeRow.phone_hash == phone_hash,
                OtpChallengeRow.consumed_at.is_(None),
            )
        ).scalars().all()
        return tuple(_otp_entity(row) for row in rows)


class _SessionRepo:
    def __init__(self, uow: SqlAlchemyIdentityUnitOfWork) -> None:
        self._uow = uow

    def save(self, record: Session) -> None:
        pending = self._uow._pending["sessions"]
        previous = pending[record.session_id][1] if record.session_id in pending else None
        if previous is None and record.version > 1:
            previous = record.version - 1
        pending[record.session_id] = (record, previous)

    def get(self, session_id: UUID) -> Session | None:
        row = self._uow._require_session().get(SessionRow, session_id)
        return _session_entity(row) if row is not None else None

    def find_by_token_hash(self, token_hash: str) -> Session | None:
        row = self._uow._require_session().execute(
            select(SessionRow).where(SessionRow.token_hash == token_hash)
        ).scalar_one_or_none()
        return _session_entity(row) if row is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[Session, ...]:
        rows = self._uow._require_session().execute(
            select(SessionRow).where(SessionRow.principal_id == principal_id)
        ).scalars().all()
        return tuple(_session_entity(row) for row in rows)


class _GrantRepo:
    def __init__(self, uow: SqlAlchemyIdentityUnitOfWork) -> None:
        self._uow = uow

    def save(self, grant: RoleGrant) -> None:
        session = self._uow._require_session()
        existed = session.get(RoleGrantRow, grant.grant_id) is not None
        pending = self._uow._pending["grants"]
        if grant.grant_id in pending:
            existed = pending[grant.grant_id][1]
        pending[grant.grant_id] = (grant, existed)

    def get(self, grant_id: UUID) -> RoleGrant | None:
        row = self._uow._require_session().get(RoleGrantRow, grant_id)
        return _grant_entity(row) if row is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[RoleGrant, ...]:
        rows = self._uow._require_session().execute(
            select(RoleGrantRow).where(RoleGrantRow.principal_id == principal_id)
        ).scalars().all()
        return tuple(_grant_entity(row) for row in rows)

    def find_active(
        self, principal_id: UUID, role: str, scope_id: UUID | None
    ) -> RoleGrant | None:
        stmt = select(RoleGrantRow).where(
            RoleGrantRow.principal_id == principal_id,
            RoleGrantRow.role == role,
            RoleGrantRow.revoked_at.is_(None),
        )
        stmt = stmt.where(
            RoleGrantRow.scope_id.is_(None) if scope_id is None
            else RoleGrantRow.scope_id == scope_id
        )
        row = self._uow._require_session().execute(stmt).scalars().first()
        return _grant_entity(row) if row is not None else None


# ------------------------------------------------------------------- mappers


def _principal_row(e: Principal) -> PrincipalRow:
    return PrincipalRow(principal_id=e.principal_id, **_principal_values(e))


def _principal_values(e: Principal) -> dict[str, object]:
    return {
        "phone_hash": e.phone_hash,
        "phone_last4": e.phone_last4,
        "status": e.status.value,
        "created_at": e.created_at,
        "status_changed_at": e.status_changed_at,
        "status_reason": e.status_reason,
        "version": e.version,
    }


def _principal_entity(row: PrincipalRow) -> Principal:
    return Principal(
        principal_id=row.principal_id,  # type: ignore[arg-type]
        phone_hash=row.phone_hash,
        phone_last4=row.phone_last4,
        status=PrincipalStatus(row.status),
        created_at=row.created_at,  # type: ignore[arg-type]
        status_changed_at=row.status_changed_at,  # type: ignore[arg-type]
        status_reason=row.status_reason,
        version=row.version,
    )


def _otp_row(e: OtpChallenge) -> OtpChallengeRow:
    return OtpChallengeRow(challenge_id=e.challenge_id, **_otp_values(e))


def _otp_values(e: OtpChallenge) -> dict[str, object]:
    return {
        "phone_hash": e.phone_hash,
        "code_hash": e.code_hash,
        "purpose": e.purpose.value,
        "issued_at": e.issued_at,
        "expires_at": e.expires_at,
        "max_attempts": e.max_attempts,
        "failed_attempts": e.failed_attempts,
        "consumed_at": e.consumed_at,
        "version": e.version,
    }


def _otp_entity(row: OtpChallengeRow) -> OtpChallenge:
    return OtpChallenge(
        challenge_id=row.challenge_id,  # type: ignore[arg-type]
        phone_hash=row.phone_hash,
        code_hash=row.code_hash,
        purpose=OtpPurpose(row.purpose),
        issued_at=row.issued_at,  # type: ignore[arg-type]
        expires_at=row.expires_at,  # type: ignore[arg-type]
        max_attempts=row.max_attempts,
        failed_attempts=row.failed_attempts,
        consumed_at=row.consumed_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _session_row(e: Session) -> SessionRow:
    return SessionRow(session_id=e.session_id, **_session_values(e))


def _session_values(e: Session) -> dict[str, object]:
    return {
        "principal_id": e.principal_id,
        "token_hash": e.token_hash,
        "device_id_hash": e.device_id_hash,
        "issued_at": e.issued_at,
        "expires_at": e.expires_at,
        "revoked_at": e.revoked_at,
        "revoked_reason": e.revoked_reason,
        "last_seen_at": e.last_seen_at,
        "version": e.version,
    }


def _session_entity(row: SessionRow) -> Session:
    return Session(
        session_id=row.session_id,  # type: ignore[arg-type]
        principal_id=row.principal_id,  # type: ignore[arg-type]
        token_hash=row.token_hash,
        device_id_hash=row.device_id_hash,
        issued_at=row.issued_at,  # type: ignore[arg-type]
        expires_at=row.expires_at,  # type: ignore[arg-type]
        revoked_at=row.revoked_at,  # type: ignore[arg-type]
        revoked_reason=row.revoked_reason,
        last_seen_at=row.last_seen_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _grant_row(e: RoleGrant) -> RoleGrantRow:
    return RoleGrantRow(grant_id=e.grant_id, **_grant_values(e))


def _grant_values(e: RoleGrant) -> dict[str, object]:
    return {
        "principal_id": e.principal_id,
        "role": e.role.value,
        "scope_kind": e.scope_kind.value,
        "scope_id": e.scope_id,
        "granted_at": e.granted_at,
        "granted_by": e.granted_by,
        "revoked_at": e.revoked_at,
        "revoked_by": e.revoked_by,
    }


def _grant_entity(row: RoleGrantRow) -> RoleGrant:
    return RoleGrant(
        grant_id=row.grant_id,  # type: ignore[arg-type]
        principal_id=row.principal_id,  # type: ignore[arg-type]
        role=Role(row.role),
        scope_kind=ScopeKind(row.scope_kind),
        scope_id=row.scope_id,  # type: ignore[arg-type]
        granted_at=row.granted_at,  # type: ignore[arg-type]
        granted_by=row.granted_by,
        revoked_at=row.revoked_at,  # type: ignore[arg-type]
        revoked_by=row.revoked_by,
    )
