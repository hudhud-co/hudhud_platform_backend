"""In-memory Identity unit of work for unit tests.

Commit/rollback are modelled with a snapshot so a rolled-back command leaves no trace —
the same property the SQLAlchemy store gets from a real transaction.
"""

from __future__ import annotations

import copy
from contextvars import ContextVar
from datetime import datetime
from uuid import UUID

from identity.domain.entities import OtpChallenge, Principal, RoleGrant, Session

_COLLECTIONS = ("principals", "otp_challenges", "sessions", "role_grants")


class InMemoryIdentityUnitOfWork:
    """Mirrors the SQLAlchemy store, including its request-scoped transaction.

    One instance is shared by every request (``app.state.unit_of_work``), so the
    open transaction cannot live on ``self``: two requests in flight would see
    each other's. It is held in a :class:`~contextvars.ContextVar`, which FastAPI
    gives a fresh copy of per request — for async endpoints directly, and for
    sync ones because anyio copies the context into the worker thread.
    """

    def __init__(self) -> None:
        self._committed: dict[str, dict] = {name: {} for name in _COLLECTIONS}
        self._tx_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "identity_memory_tx", default=None
        )
        self._principals = _PrincipalRepo(self)
        self._otp = _OtpRepo(self)
        self._sessions = _SessionRepo(self)
        self._grants = _GrantRepo(self)

    # ------------------------------------------------------------- transaction

    @property
    def _tx(self) -> dict[str, dict] | None:
        return self._tx_var.get()

    def begin(self) -> None:
        if self._tx_var.get() is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._tx_var.set(copy.deepcopy(self._committed))

    def commit(self) -> None:
        tx = self._tx_var.get()
        if tx is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._committed = tx
        self._tx_var.set(None)

    def rollback(self) -> None:
        self._tx_var.set(None)

    def _working(self, name: str) -> dict:
        """Refuse a read or a write outside a transaction, exactly as the store does.

        This used to fall back to the committed state, which made the double *more*
        permissive than SQLAlchemy — so a service method that forgot to open a
        transaction passed every unit test and then failed against PostgreSQL. It cost
        real debugging once; a double that disagrees with the thing it stands in for is
        worse than no double.
        """
        if self._tx is None:
            msg = (
                f"no active transaction: {name} was accessed outside begin()/commit(). "
                "The SQLAlchemy store raises here too."
            )
            raise RuntimeError(msg)
        return self._tx[name]

    # -------------------------------------------------------------- repositories

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


class _PrincipalRepo:
    def __init__(self, store: InMemoryIdentityUnitOfWork) -> None:
        self._store = store

    def save(self, principal: Principal) -> None:
        self._store._working("principals")[principal.principal_id] = copy.deepcopy(principal)

    def get(self, principal_id: UUID) -> Principal | None:
        found = self._store._working("principals").get(principal_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_phone_hash(self, phone_hash: str) -> Principal | None:
        for principal in self._store._working("principals").values():
            if principal.phone_hash == phone_hash:
                return copy.deepcopy(principal)
        return None


class _OtpRepo:
    def __init__(self, store: InMemoryIdentityUnitOfWork) -> None:
        self._store = store

    def save(self, challenge: OtpChallenge) -> None:
        self._store._working("otp_challenges")[challenge.challenge_id] = copy.deepcopy(
            challenge
        )

    def get(self, challenge_id: UUID) -> OtpChallenge | None:
        found = self._store._working("otp_challenges").get(challenge_id)
        return copy.deepcopy(found) if found is not None else None

    def count_issued_since(self, phone_hash: str, since: datetime) -> int:
        return sum(
            1
            for c in self._store._working("otp_challenges").values()
            if c.phone_hash == phone_hash and c.issued_at >= since
        )

    def list_open_for_phone(self, phone_hash: str) -> tuple[OtpChallenge, ...]:
        return tuple(
            copy.deepcopy(c)
            for c in self._store._working("otp_challenges").values()
            if c.phone_hash == phone_hash and c.consumed_at is None
        )


class _SessionRepo:
    def __init__(self, store: InMemoryIdentityUnitOfWork) -> None:
        self._store = store

    def save(self, session: Session) -> None:
        self._store._working("sessions")[session.session_id] = copy.deepcopy(session)

    def get(self, session_id: UUID) -> Session | None:
        found = self._store._working("sessions").get(session_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_token_hash(self, token_hash: str) -> Session | None:
        for session in self._store._working("sessions").values():
            if session.token_hash == token_hash:
                return copy.deepcopy(session)
        return None

    def list_for_principal(self, principal_id: UUID) -> tuple[Session, ...]:
        return tuple(
            copy.deepcopy(s)
            for s in self._store._working("sessions").values()
            if s.principal_id == principal_id
        )


class _GrantRepo:
    def __init__(self, store: InMemoryIdentityUnitOfWork) -> None:
        self._store = store

    def save(self, grant: RoleGrant) -> None:
        self._store._working("role_grants")[grant.grant_id] = copy.deepcopy(grant)

    def get(self, grant_id: UUID) -> RoleGrant | None:
        found = self._store._working("role_grants").get(grant_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[RoleGrant, ...]:
        return tuple(
            copy.deepcopy(g)
            for g in self._store._working("role_grants").values()
            if g.principal_id == principal_id
        )

    def find_active(
        self, principal_id: UUID, role: str, scope_id: UUID | None
    ) -> RoleGrant | None:
        for grant in self._store._working("role_grants").values():
            if (
                grant.principal_id == principal_id
                and grant.role.value == role
                and grant.scope_id == scope_id
                and grant.revoked_at is None
            ):
                return copy.deepcopy(grant)
        return None
