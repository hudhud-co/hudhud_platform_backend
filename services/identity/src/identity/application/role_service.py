"""Role grant administration.

Granting a privileged role is an administrative act, never a self-service one. Identity
records *that* a principal holds a role scoped to a merchant or a hub; it never validates
the scope id against another context's tables, because that would be cross-context reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from identity.domain.entities import RoleGrant
from identity.domain.errors import (
    PrincipalNotFound,
    RoleGrantNotPermitted,
    RoleGrantScopeInvalid,
)
from identity.domain.value_objects import (
    PrincipalStatus,
    Role,
    ScopeKind,
)
from identity.ports.repository import IdentityUnitOfWork

#: Roles that only make sense against a concrete scope, and the scope they require.
REQUIRED_SCOPE: dict[Role, ScopeKind] = {
    Role.MERCHANT_MEMBER: ScopeKind.MERCHANT,
    Role.HUB_OPERATOR: ScopeKind.HUB,
    Role.HUB_CASHIER: ScopeKind.HUB,
}


@dataclass(frozen=True, slots=True)
class GrantRoleCommand:
    principal_id: UUID
    role: Role | str
    granted_by: str
    occurred_at: datetime
    scope_kind: ScopeKind | str = ScopeKind.GLOBAL
    scope_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RoleGrantResult:
    grant: RoleGrant
    idempotent_replay: bool = False


class RoleService:
    def __init__(self, unit_of_work: IdentityUnitOfWork) -> None:
        self._uow = unit_of_work

    def grant(self, command: GrantRoleCommand) -> RoleGrantResult:
        role = _coerce_role(command.role)
        scope_kind = _coerce_scope(command.scope_kind)
        required = REQUIRED_SCOPE.get(role)
        if required is not None:
            if scope_kind is not required or command.scope_id is None:
                raise RoleGrantScopeInvalid(role=role.value, scope_kind=scope_kind.value)
        elif scope_kind is not ScopeKind.GLOBAL:
            raise RoleGrantScopeInvalid(role=role.value, scope_kind=scope_kind.value)

        self._uow.begin()
        try:
            principal = self._uow.principals.get(command.principal_id)
            if principal is None:
                raise PrincipalNotFound(str(command.principal_id))
            if principal.status is PrincipalStatus.CLOSED:
                raise RoleGrantNotPermitted(
                    role=role.value, reason="principal is closed"
                )
            existing = self._uow.role_grants.find_active(
                command.principal_id, role.value, command.scope_id
            )
            if existing is not None:
                result = RoleGrantResult(grant=existing, idempotent_replay=True)
            else:
                grant = RoleGrant(
                    grant_id=uuid4(),
                    principal_id=command.principal_id,
                    role=role,
                    scope_kind=scope_kind,
                    scope_id=command.scope_id,
                    granted_at=command.occurred_at,
                    granted_by=command.granted_by,
                )
                self._uow.role_grants.save(grant)
                result = RoleGrantResult(grant=grant)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def revoke(
        self, *, grant_id: UUID, revoked_by: str, occurred_at: datetime
    ) -> RoleGrant | None:
        self._uow.begin()
        try:
            grant = self._uow.role_grants.get(grant_id)
            if grant is not None and grant.revoked_at is None:
                grant.revoked_at = occurred_at
                grant.revoked_by = revoked_by
                self._uow.role_grants.save(grant)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return grant

    def set_principal_status(
        self,
        *,
        principal_id: UUID,
        status: PrincipalStatus | str,
        reason: str,
        occurred_at: datetime,
    ) -> None:
        resolved = status if isinstance(status, PrincipalStatus) else PrincipalStatus(status)
        self._uow.begin()
        try:
            principal = self._uow.principals.get(principal_id)
            if principal is None:
                raise PrincipalNotFound(str(principal_id))
            principal.status = resolved
            principal.status_changed_at = occurred_at
            principal.status_reason = reason
            principal.version += 1
            self._uow.principals.save(principal)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()

    def list_grants(self, principal_id: UUID) -> tuple[RoleGrant, ...]:
        return self._uow.role_grants.list_for_principal(principal_id)


def _coerce_role(value: Role | str) -> Role:
    if isinstance(value, Role):
        return value
    try:
        return Role(str(value).strip().upper())
    except ValueError as exc:
        raise RoleGrantNotPermitted(role=str(value), reason="unknown role") from exc


def _coerce_scope(value: ScopeKind | str) -> ScopeKind:
    if isinstance(value, ScopeKind):
        return value
    try:
        return ScopeKind(str(value).strip().upper())
    except ValueError as exc:
        raise RoleGrantScopeInvalid(role="", scope_kind=str(value)) from exc
