"""Token introspection — the boundary every other service authenticates through.

This is the single point where a bearer token becomes a principal with roles. It is
service-to-service only: an end user must never be able to ask who a token belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from identity.domain.errors import ServiceCredentialRejected
from identity.domain.security import hash_device_id, hash_session_token
from identity.domain.value_objects import PrincipalStatus, Role, ScopeKind
from identity.ports.repository import IdentityUnitOfWork
from identity.ports.service_credentials import ServiceCredentialVerifier


@dataclass(frozen=True, slots=True)
class GrantView:
    role: Role
    scope_kind: ScopeKind
    scope_id: UUID | None


@dataclass(frozen=True, slots=True)
class IntrospectionResult:
    """What a bearer token currently proves. ``active=False`` proves nothing else."""

    active: bool
    principal_id: UUID | None = None
    status: PrincipalStatus | None = None
    roles: tuple[Role, ...] = ()
    grants: tuple[GrantView, ...] = ()
    session_id: UUID | None = None
    expires_at: datetime | None = None
    inactive_reason: str | None = None

    @property
    def merchant_ids(self) -> frozenset[UUID]:
        return frozenset(
            g.scope_id
            for g in self.grants
            if g.scope_kind is ScopeKind.MERCHANT and g.scope_id is not None
        )

    @property
    def hub_ids(self) -> frozenset[UUID]:
        return frozenset(
            g.scope_id
            for g in self.grants
            if g.scope_kind is ScopeKind.HUB and g.scope_id is not None
        )


_INACTIVE = IntrospectionResult(active=False, inactive_reason="not_active")


class IntrospectionService:
    """Resolve a bearer token to a principal, its status and its live role grants."""

    def __init__(
        self,
        unit_of_work: IdentityUnitOfWork,
        *,
        signing_key: str,
        service_credentials: ServiceCredentialVerifier,
    ) -> None:
        self._uow = unit_of_work
        self._signing_key = signing_key
        self._service_credentials = service_credentials

    def introspect(
        self,
        *,
        token: str,
        service_credential: str,
        now: datetime,
        device_id: str | None = None,
    ) -> IntrospectionResult:
        caller = self._service_credentials.verify(service_credential)
        if caller is None:
            raise ServiceCredentialRejected()

        if not token.strip():
            return _INACTIVE

        # Reading the session, the principal and the grants is three reads that must
        # agree with each other: a principal suspended between the first and the third
        # would otherwise be introspected as active. They belong in one transaction —
        # and the SQLAlchemy store requires one, which is how this was found.
        self._uow.begin()
        try:
            result = self._resolve(token=token, now=now, device_id=device_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return result

    def _resolve(
        self, *, token: str, now: datetime, device_id: str | None
    ) -> IntrospectionResult:
        session = self._uow.sessions.find_by_token_hash(
            hash_session_token(token, signing_key=self._signing_key)
        )
        if session is None:
            return _INACTIVE
        if session.revoked_at is not None:
            return _INACTIVE
        if now >= session.expires_at:
            return _INACTIVE
        if session.device_id_hash is not None and device_id is not None:
            presented = hash_device_id(device_id, signing_key=self._signing_key)
            if presented != session.device_id_hash:
                # A token replayed from another device proves nothing.
                return _INACTIVE

        principal = self._uow.principals.get(session.principal_id)
        if principal is None or not principal.can_authenticate:
            return _INACTIVE

        grants = tuple(
            GrantView(role=g.role, scope_kind=g.scope_kind, scope_id=g.scope_id)
            for g in self._uow.role_grants.list_for_principal(principal.principal_id)
            if g.is_active
        )
        return IntrospectionResult(
            active=True,
            principal_id=principal.principal_id,
            status=principal.status,
            roles=tuple(sorted({g.role for g in grants})),
            grants=grants,
            session_id=session.session_id,
            expires_at=session.expires_at,
        )
