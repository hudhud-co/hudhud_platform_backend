"""OTP sign-in and session lifecycle.

Two rules shape everything here. First, a failed authentication must never tell the caller
*why* it failed — otherwise the endpoint becomes an oracle for which phone numbers are
registered. Second, the counters that protect against brute force must survive the
rejection they caused, so a failed attempt is committed even though the request fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from identity.domain.entities import OtpChallenge, Principal, RoleGrant, Session
from identity.domain.errors import (
    InvalidPhoneNumber,
    OtpRateLimited,
    OtpVerificationFailed,
    PrincipalNotAuthenticable,
)
from identity.domain.security import (
    generate_otp_code,
    generate_session_token,
    hash_device_id,
    hash_otp_code,
    hash_phone,
    hash_session_token,
    normalize_phone,
    phone_last4,
    verify_otp_code,
)
from identity.domain.value_objects import (
    SELF_SERVICE_ROLE,
    OtpFailureReason,
    OtpPurpose,
    PrincipalStatus,
    Role,
    ScopeKind,
)
from identity.ports.otp_delivery import OtpDeliveryPort
from identity.ports.repository import IdentityUnitOfWork


@dataclass(frozen=True, slots=True)
class BootstrapPolicy:
    """How the very first Operations principal comes into existence.

    A fresh deployment has no operator, so no one can grant the first privileged role —
    the administration endpoints require Operations to call them. Exactly one phone
    number, supplied as configuration, receives the Operations role when its principal is
    first created. It is deliberately narrow: it applies only at creation, only to that
    one number, and it grants nothing to anyone who signs in later.
    """

    operations_phone: str | None = None


@dataclass(frozen=True, slots=True)
class OtpPolicy:
    code_ttl_seconds: int = 300
    max_attempts: int = 5
    max_codes_per_window: int = 5
    rate_limit_window_seconds: int = 900
    session_ttl_seconds: int = 60 * 60 * 24 * 30


@dataclass(frozen=True, slots=True)
class RequestOtpCommand:
    phone: str
    occurred_at: datetime
    purpose: OtpPurpose = OtpPurpose.SIGN_IN


@dataclass(frozen=True, slots=True)
class RequestOtpResult:
    challenge_id: UUID
    phone_last4: str
    expires_at: datetime
    #: True when this phone had no principal before. Never returned over HTTP — it would
    #: disclose whether a number is registered.
    principal_created: bool


@dataclass(frozen=True, slots=True)
class VerifyOtpCommand:
    challenge_id: UUID
    code: str
    occurred_at: datetime
    device_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    session_id: UUID
    principal_id: UUID
    #: The bearer token in plaintext. This is the only moment it exists in this form.
    token: str
    issued_at: datetime
    expires_at: datetime
    status: PrincipalStatus
    roles: tuple[Role, ...]


class AuthenticationService:
    """Issue and verify one-time codes, and mint sessions from them."""

    def __init__(
        self,
        unit_of_work: IdentityUnitOfWork,
        *,
        signing_key: str,
        otp_delivery: OtpDeliveryPort,
        policy: OtpPolicy | None = None,
        bootstrap: BootstrapPolicy | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._signing_key = signing_key
        self._delivery = otp_delivery
        self._policy = policy or OtpPolicy()
        self._bootstrap = bootstrap or BootstrapPolicy()

    # ------------------------------------------------------------------ request

    def request_code(self, command: RequestOtpCommand) -> RequestOtpResult:
        try:
            normalized = normalize_phone(command.phone)
        except ValueError as exc:
            raise InvalidPhoneNumber(str(exc)) from exc

        phone_hash = hash_phone(normalized, signing_key=self._signing_key)
        last4 = phone_last4(normalized)

        self._uow.begin()
        try:
            window_start = command.occurred_at - timedelta(
                seconds=self._policy.rate_limit_window_seconds
            )
            issued = self._uow.otp_challenges.count_issued_since(phone_hash, window_start)
            if issued >= self._policy.max_codes_per_window:
                raise OtpRateLimited(
                    retry_after_seconds=self._policy.rate_limit_window_seconds
                )

            principal = self._uow.principals.find_by_phone_hash(phone_hash)
            created = principal is None
            if principal is None:
                # Signing in for the first time creates the principal. It carries no
                # profile and only the self-service role; everything else is granted.
                principal = Principal(
                    principal_id=uuid4(),
                    phone_hash=phone_hash,
                    phone_last4=last4,
                    status=PrincipalStatus.ACTIVE,
                    created_at=command.occurred_at,
                )
                self._uow.principals.save(principal)
                self._uow.role_grants.save(
                    RoleGrant(
                        grant_id=uuid4(),
                        principal_id=principal.principal_id,
                        role=SELF_SERVICE_ROLE,
                        scope_kind=ScopeKind.GLOBAL,
                        scope_id=None,
                        granted_at=command.occurred_at,
                        granted_by="self_service_signup",
                    )
                )
                if self._is_bootstrap_operator(normalized):
                    self._uow.role_grants.save(
                        RoleGrant(
                            grant_id=uuid4(),
                            principal_id=principal.principal_id,
                            role=Role.OPERATIONS,
                            scope_kind=ScopeKind.GLOBAL,
                            scope_id=None,
                            granted_at=command.occurred_at,
                            granted_by="configured_bootstrap_operator",
                        )
                    )

            # Requesting a new code invalidates any code still outstanding for this phone,
            # so two live codes can never authenticate the same person.
            for open_challenge in self._uow.otp_challenges.list_open_for_phone(phone_hash):
                if open_challenge.consumed_at is None:
                    open_challenge.consumed_at = command.occurred_at
                    open_challenge.version += 1
                    self._uow.otp_challenges.save(open_challenge)

            challenge_id = uuid4()
            code = generate_otp_code()
            challenge = OtpChallenge(
                challenge_id=challenge_id,
                phone_hash=phone_hash,
                code_hash=hash_otp_code(
                    code, challenge_id=str(challenge_id), signing_key=self._signing_key
                ),
                purpose=command.purpose,
                issued_at=command.occurred_at,
                expires_at=command.occurred_at
                + timedelta(seconds=self._policy.code_ttl_seconds),
                max_attempts=self._policy.max_attempts,
            )
            self._uow.otp_challenges.save(challenge)
            # Deliver inside the transaction: if delivery fails, no challenge exists, so
            # the user is never left waiting for a code that was recorded but not sent.
            self._delivery.send_code(
                phone_last4=last4, code=code, reference=str(challenge_id)
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return RequestOtpResult(
            challenge_id=challenge_id,
            phone_last4=last4,
            expires_at=challenge.expires_at,
            principal_created=created,
        )

    # ------------------------------------------------------------------- verify

    def verify_code(self, command: VerifyOtpCommand) -> AuthenticatedSession:
        """Verify a code and mint a session.

        A rejection still commits: the attempt counter and the consumption marker are the
        brute-force defence, and rolling them back would make the limit unenforceable.
        """
        self._uow.begin()
        deferred: OtpFailureReason | None = None
        session: AuthenticatedSession | None = None
        try:
            challenge = self._uow.otp_challenges.get(command.challenge_id)
            if challenge is None:
                deferred = OtpFailureReason.NOT_FOUND
            elif challenge.is_consumed:
                deferred = OtpFailureReason.ALREADY_CONSUMED
            elif challenge.is_expired(command.occurred_at):
                deferred = OtpFailureReason.EXPIRED
            elif challenge.attempts_exhausted:
                deferred = OtpFailureReason.TOO_MANY_ATTEMPTS
            elif not verify_otp_code(
                command.code,
                challenge_id=str(challenge.challenge_id),
                signing_key=self._signing_key,
                expected=challenge.code_hash,
            ):
                challenge.failed_attempts += 1
                challenge.version += 1
                if challenge.attempts_exhausted:
                    # Burn the challenge rather than leaving it to expire on its own.
                    challenge.consumed_at = command.occurred_at
                self._uow.otp_challenges.save(challenge)
                deferred = OtpFailureReason.CODE_MISMATCH
            else:
                principal = self._uow.principals.find_by_phone_hash(challenge.phone_hash)
                if principal is None or not principal.can_authenticate:
                    challenge.consumed_at = command.occurred_at
                    challenge.version += 1
                    self._uow.otp_challenges.save(challenge)
                    deferred = OtpFailureReason.PRINCIPAL_NOT_AUTHENTICABLE
                else:
                    challenge.consumed_at = command.occurred_at
                    challenge.version += 1
                    self._uow.otp_challenges.save(challenge)
                    session = self._mint_session(
                        principal=principal,
                        device_id=command.device_id,
                        now=command.occurred_at,
                    )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()

        if deferred is not None:
            # One message for every failure mode: the endpoint must not reveal whether a
            # phone is registered, whether a code expired, or how many tries remain.
            raise OtpVerificationFailed(deferred.value)
        assert session is not None
        return session

    def _is_bootstrap_operator(self, normalized_phone: str) -> bool:
        configured = self._bootstrap.operations_phone
        if not configured:
            return False
        try:
            return normalize_phone(configured) == normalized_phone
        except ValueError:
            # A malformed bootstrap number grants nothing rather than matching loosely.
            return False

    def _mint_session(
        self, *, principal: Principal, device_id: str | None, now: datetime
    ) -> AuthenticatedSession:
        token = generate_session_token()
        record = Session(
            session_id=uuid4(),
            principal_id=principal.principal_id,
            token_hash=hash_session_token(token, signing_key=self._signing_key),
            device_id_hash=(
                hash_device_id(device_id, signing_key=self._signing_key)
                if device_id
                else None
            ),
            issued_at=now,
            expires_at=now + timedelta(seconds=self._policy.session_ttl_seconds),
        )
        self._uow.sessions.save(record)
        roles = tuple(
            sorted(
                {
                    grant.role
                    for grant in self._uow.role_grants.list_for_principal(
                        principal.principal_id
                    )
                    if grant.is_active
                }
            )
        )
        return AuthenticatedSession(
            session_id=record.session_id,
            principal_id=principal.principal_id,
            token=token,
            issued_at=record.issued_at,
            expires_at=record.expires_at,
            status=principal.status,
            roles=roles,
        )

    # ------------------------------------------------------------------ revoke

    def revoke_session(self, *, session_id: UUID, now: datetime, reason: str) -> None:
        self._uow.begin()
        try:
            record = self._uow.sessions.get(session_id)
            if record is not None and record.revoked_at is None:
                record.revoked_at = now
                record.revoked_reason = reason
                record.version += 1
                self._uow.sessions.save(record)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()

    def revoke_all_for_principal(
        self, *, principal_id: UUID, now: datetime, reason: str
    ) -> int:
        self._uow.begin()
        revoked = 0
        try:
            for record in self._uow.sessions.list_for_principal(principal_id):
                if record.revoked_at is None:
                    record.revoked_at = now
                    record.revoked_reason = reason
                    record.version += 1
                    self._uow.sessions.save(record)
                    revoked += 1
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return revoked


def assert_authenticable(principal: Principal) -> None:
    if not principal.can_authenticate:
        raise PrincipalNotAuthenticable(
            principal_id=str(principal.principal_id), status=principal.status.value
        )
