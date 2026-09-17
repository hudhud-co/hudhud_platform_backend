"""Value objects for authentication, principals and role grants."""

from __future__ import annotations

from enum import StrEnum


class PrincipalStatus(StrEnum):
    """Lifecycle of an authenticatable principal.

    ``PENDING_VERIFICATION`` exists because a driver applicant may sign in and see their
    application state, but may not be assigned work until a HUDHUD office completes
    physical verification (Driver App v8 ``reg4``).
    """

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


#: Statuses that may complete an authentication exchange.
AUTHENTICABLE_STATUSES: frozenset[PrincipalStatus] = frozenset(
    {PrincipalStatus.PENDING_VERIFICATION, PrincipalStatus.ACTIVE}
)


class Role(StrEnum):
    """Role classes the platform distinguishes.

    Identity grants roles; it never stores *domain* membership. Which stores a merchant
    member belongs to is owned by the merchant service, which hub a hub operator works at
    is owned by the hub service, and a driver's work session is owned by pickup.
    """

    CUSTOMER = "CUSTOMER"
    MERCHANT_MEMBER = "MERCHANT_MEMBER"
    PICKUP_DRIVER = "PICKUP_DRIVER"
    DELIVERY_DRIVER = "DELIVERY_DRIVER"
    HUB_OPERATOR = "HUB_OPERATOR"
    HUB_CASHIER = "HUB_CASHIER"
    ACCOUNTANT = "ACCOUNTANT"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


#: Roles a principal may never grant to itself or obtain by signing up.
PRIVILEGED_ROLES: frozenset[Role] = frozenset(
    {
        Role.HUB_OPERATOR,
        Role.HUB_CASHIER,
        Role.ACCOUNTANT,
        Role.SUPPORT,
        Role.OPERATIONS,
        Role.PICKUP_DRIVER,
        Role.DELIVERY_DRIVER,
    }
)

#: The only role a self-service sign-up may produce.
SELF_SERVICE_ROLE: Role = Role.CUSTOMER


class OtpPurpose(StrEnum):
    SIGN_IN = "SIGN_IN"
    STEP_UP = "STEP_UP"


class OtpFailureReason(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    EXPIRED = "EXPIRED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    TOO_MANY_ATTEMPTS = "TOO_MANY_ATTEMPTS"
    CODE_MISMATCH = "CODE_MISMATCH"
    PRINCIPAL_NOT_AUTHENTICABLE = "PRINCIPAL_NOT_AUTHENTICABLE"


class ScopeKind(StrEnum):
    """What a role grant is scoped to, when it is scoped at all.

    The scope *id* is an opaque reference owned by another context — Identity never
    resolves or validates it against that context's tables.
    """

    GLOBAL = "GLOBAL"
    MERCHANT = "MERCHANT"
    HUB = "HUB"
