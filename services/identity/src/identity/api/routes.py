"""Identity HTTP adapter.

Routers map schema to command and back. There is no business logic here: the decision to
reject a code, rate-limit a phone or refuse a grant lives in the application services.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from identity.api.errors import raise_http_for_domain_error
from identity.api.schemas import (
    GrantResponse,
    GrantRoleRequest,
    IntrospectRequest,
    IntrospectResponse,
    MeResponse,
    PrincipalStatusRequest,
    RequestOtpRequest,
    RequestOtpResponse,
    RoleGrantResponse,
    SessionResponse,
    VerifyOtpRequest,
)
from identity.application.authentication_service import (
    AuthenticationService,
    RequestOtpCommand,
    VerifyOtpCommand,
)
from identity.application.introspection_service import IntrospectionService
from identity.application.role_service import GrantRoleCommand, RoleService
from identity.domain.errors import PrincipalNotFound
from identity.domain.value_objects import PRIVILEGED_ROLES, Role

router = APIRouter(prefix="/identity", tags=["identity"])


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _service(request: Request, name: str, label: str) -> object:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_auth(request: Request) -> AuthenticationService:
    return _service(request, "authentication_service", "authentication")  # type: ignore[return-value]


def get_introspection(request: Request) -> IntrospectionService:
    return _service(request, "introspection_service", "introspection")  # type: ignore[return-value]


def get_roles(request: Request) -> RoleService:
    return _service(request, "role_service", "role")  # type: ignore[return-value]


Auth = Annotated[AuthenticationService, Depends(get_auth)]
Introspect = Annotated[IntrospectionService, Depends(get_introspection)]
Roles = Annotated[RoleService, Depends(get_roles)]
# Both are declared optional and defaulted at each call site, so a missing header is an
# authentication failure (401/403) rather than a schema validation failure (422).
ServiceCredential = Annotated[str | None, Header(alias="X-Service-Credential")]
BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _bearer(header: str | None) -> str:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail={"code": "missing_bearer_token"}
        )
    return header.split(" ", 1)[1].strip()


# ------------------------------------------------------------------ sign-in


@router.post("/otp/request", response_model=RequestOtpResponse)
def request_otp(body: RequestOtpRequest, service: Auth) -> RequestOtpResponse:
    try:
        result = service.request_code(
            RequestOtpCommand(phone=body.phone, occurred_at=_now())
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    # principal_created is deliberately dropped: disclosing it would reveal whether a
    # phone number is already registered.
    return RequestOtpResponse(
        challenge_id=result.challenge_id,
        phone_last4=result.phone_last4,
        expires_at=result.expires_at,
    )


@router.post("/otp/verify", response_model=SessionResponse)
def verify_otp(body: VerifyOtpRequest, service: Auth) -> SessionResponse:
    try:
        session = service.verify_code(
            VerifyOtpCommand(
                challenge_id=body.challenge_id,
                code=body.code,
                occurred_at=_now(),
                device_id=body.device_id,
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return SessionResponse(
        access_token=session.token,
        session_id=session.session_id,
        principal_id=session.principal_id,
        status=session.status.value,
        roles=[role.value for role in session.roles],
        expires_at=session.expires_at,
    )


@router.post("/sessions/revoke", status_code=204)
def revoke_session(
    service: Auth,
    introspection: Introspect,
    authorization: BearerHeader = None,
    service_credential: ServiceCredential = None,
) -> None:
    """Sign out. Resolving the token needs the service's own credential."""
    token = _bearer(authorization)
    try:
        result = introspection.introspect(
            token=token, service_credential=service_credential or "", now=_now()
        )
        if result.active and result.session_id is not None:
            service.revoke_session(
                session_id=result.session_id, now=_now(), reason="user_signed_out"
            )
    except Exception as exc:
        raise_http_for_domain_error(exc)


# ----------------------------------------------------------- introspection


@router.post("/tokens/introspect", response_model=IntrospectResponse)
def introspect_token(
    body: IntrospectRequest,
    service: Introspect,
    service_credential: ServiceCredential = None,
) -> IntrospectResponse:
    """Service-to-service only. An end user must never learn who a token belongs to."""
    try:
        result = service.introspect(
            token=body.token,
            service_credential=service_credential or "",
            now=_now(),
            device_id=body.device_id,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    if not result.active:
        return IntrospectResponse(active=False)
    return IntrospectResponse(
        active=True,
        principal_id=result.principal_id,
        status=result.status.value if result.status else None,
        roles=[role.value for role in result.roles],
        grants=[
            GrantResponse(
                role=g.role.value, scope_kind=g.scope_kind.value, scope_id=g.scope_id
            )
            for g in result.grants
        ],
        session_id=result.session_id,
        expires_at=result.expires_at,
    )


@router.get("/me", response_model=MeResponse)
def read_me(
    introspection: Introspect,
    request: Request,
    authorization: BearerHeader = None,
    service_credential: ServiceCredential = None,
) -> MeResponse:
    token = _bearer(authorization)
    try:
        result = introspection.introspect(
            token=token, service_credential=service_credential or "", now=_now()
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    if not result.active or result.principal_id is None:
        raise HTTPException(status_code=401, detail={"code": "session_not_active"})
    uow = request.app.state.unit_of_work
    uow.begin()
    try:
        principal = uow.principals.get(result.principal_id)
    finally:
        uow.rollback()
    if principal is None:
        raise HTTPException(status_code=404, detail={"code": "principal_not_found"})
    return MeResponse(
        principal_id=principal.principal_id,
        phone_last4=principal.phone_last4,
        status=principal.status.value,
        roles=[role.value for role in result.roles],
    )


# -------------------------------------------------------------- administration


def _require_operations(introspection: IntrospectionService, token: str, credential: str):
    result = introspection.introspect(
        token=token, service_credential=credential, now=_now()
    )
    if not result.active:
        raise HTTPException(status_code=401, detail={"code": "session_not_active"})
    if Role.OPERATIONS not in result.roles:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return result


@router.post("/principals/{principal_id}/roles", response_model=RoleGrantResponse)
def grant_role(
    principal_id: UUID,
    body: GrantRoleRequest,
    service: Roles,
    introspection: Introspect,
    authorization: BearerHeader = None,
    service_credential: ServiceCredential = None,
) -> RoleGrantResponse:
    """Only Operations may grant. A privileged role is never self-service."""
    token = _bearer(authorization)
    try:
        actor = _require_operations(introspection, token, service_credential or "")
        result = service.grant(
            GrantRoleCommand(
                principal_id=principal_id,
                role=body.role,
                granted_by=str(actor.principal_id),
                occurred_at=_now(),
                scope_kind=body.scope_kind,
                scope_id=body.scope_id,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http_for_domain_error(exc)
    grant = result.grant
    return RoleGrantResponse(
        grant_id=grant.grant_id,
        principal_id=grant.principal_id,
        role=grant.role.value,
        scope_kind=grant.scope_kind.value,
        scope_id=grant.scope_id,
        granted_at=grant.granted_at,
        revoked_at=grant.revoked_at,
        idempotent_replay=result.idempotent_replay,
    )


@router.post("/role-grants/{grant_id}/revoke", response_model=RoleGrantResponse)
def revoke_role(
    grant_id: UUID,
    service: Roles,
    introspection: Introspect,
    authorization: BearerHeader = None,
    service_credential: ServiceCredential = None,
) -> RoleGrantResponse:
    token = _bearer(authorization)
    try:
        actor = _require_operations(introspection, token, service_credential or "")
        grant = service.revoke(
            grant_id=grant_id, revoked_by=str(actor.principal_id), occurred_at=_now()
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http_for_domain_error(exc)
    if grant is None:
        raise HTTPException(status_code=404, detail={"code": "role_grant_not_found"})
    return RoleGrantResponse(
        grant_id=grant.grant_id,
        principal_id=grant.principal_id,
        role=grant.role.value,
        scope_kind=grant.scope_kind.value,
        scope_id=grant.scope_id,
        granted_at=grant.granted_at,
        revoked_at=grant.revoked_at,
    )


@router.post("/principals/{principal_id}/status", status_code=204)
def set_principal_status(
    principal_id: UUID,
    body: PrincipalStatusRequest,
    service: Roles,
    auth: Auth,
    introspection: Introspect,
    authorization: BearerHeader = None,
    service_credential: ServiceCredential = None,
) -> None:
    """Suspending a principal must also end its live sessions, or the block is cosmetic."""
    token = _bearer(authorization)
    try:
        _require_operations(introspection, token, service_credential or "")
        service.set_principal_status(
            principal_id=principal_id,
            status=body.status,
            reason=body.reason,
            occurred_at=_now(),
        )
        if body.status.upper() in {"SUSPENDED", "CLOSED"}:
            auth.revoke_all_for_principal(
                principal_id=principal_id, now=_now(), reason="principal_status_changed"
            )
    except HTTPException:
        raise
    except PrincipalNotFound as exc:
        raise_http_for_domain_error(exc)
    except Exception as exc:
        raise_http_for_domain_error(exc)


__all__ = ["router", "PRIVILEGED_ROLES"]
