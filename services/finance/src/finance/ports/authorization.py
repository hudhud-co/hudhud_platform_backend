"""Authorization boundary for Finance commands.

ADR-0012 puts the rule plainly: a driver may record their own deposit, only a hub cashier
or an accountant may confirm one, and only Operations may approve a payout. Each of those
is a different person for a reason — the one who hands the money over is never the one who
says it arrived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class FinanceCommand(StrEnum):
    CASH_ACCOUNT_READ = "cash_account:read"
    CASH_LIMIT_SET = "cash_limit:set"
    COD_RECORD = "cod:record"
    DEPOSIT_SUBMIT = "deposit:submit"
    DEPOSIT_DECIDE = "deposit:decide"
    DEPOSIT_READ = "deposit:read"
    PAYOUT_REQUEST = "payout:request"
    PAYOUT_DECIDE = "payout:decide"
    PAYOUT_PAY = "payout:pay"
    PAYOUT_READ = "payout:read"
    RECONCILIATION_OPEN = "reconciliation:open"
    RECONCILIATION_RESOLVE = "reconciliation:resolve"
    RECONCILIATION_READ = "reconciliation:read"
    MERCHANT_BALANCE_READ = "merchant_balance:read"
    CASH_EXPOSURE_READ = "cash_exposure:read"
    REFUND_RECOGNISE = "refund:recognise"
    LEDGER_READ = "ledger:read"


class FinanceRole(StrEnum):
    LAST_MILE_DRIVER = "LAST_MILE_DRIVER"
    PICKUP_DRIVER = "PICKUP_DRIVER"
    MERCHANT_OWNER = "MERCHANT_OWNER"
    HUB_CASHIER = "HUB_CASHIER"
    ACCOUNTANT = "ACCOUNTANT"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class FinanceActor:
    principal_id: UUID
    roles: frozenset[FinanceRole] = field(default_factory=frozenset)
    #: Which merchant this actor speaks for, when they speak for one.
    merchant_id: UUID | None = None

    def has_role(self, role: FinanceRole) -> bool:
        return role in self.roles

    @property
    def is_driver(self) -> bool:
        return bool(
            self.roles
            & {FinanceRole.LAST_MILE_DRIVER, FinanceRole.PICKUP_DRIVER}
        )

    @property
    def is_operations(self) -> bool:
        return FinanceRole.OPERATIONS in self.roles

    @property
    def is_accountant(self) -> bool:
        return FinanceRole.ACCOUNTANT in self.roles

    @property
    def is_cashier(self) -> bool:
        return FinanceRole.HUB_CASHIER in self.roles

    @property
    def may_verify_an_exchange_receipt(self) -> bool:
        """v6.3 p.31 names an accountant specifically."""
        return self.is_accountant

    @property
    def may_confirm_a_hub_deposit(self) -> bool:
        """The cashier who took the cash, or an accountant reviewing afterwards."""
        return self.is_cashier or self.is_accountant

    @property
    def may_approve_a_payout(self) -> bool:
        """ADR-0012 — only Operations."""
        return self.is_operations

    @property
    def may_resolve_a_mismatch(self) -> bool:
        return self.is_operations

    @property
    def may_see_platform_cash_exposure(self) -> bool:
        """OPS-04 — a driver sees their own cash, not everyone's."""
        return self.is_operations or self.is_accountant

    def owns(self, principal_id: UUID) -> bool:
        return self.principal_id == principal_id


@dataclass(frozen=True, slots=True)
class FinanceAccessDecision:
    outcome: AuthorizationOutcome
    actor: FinanceActor | None = None

    @staticmethod
    def allow(actor: FinanceActor) -> FinanceAccessDecision:
        return FinanceAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> FinanceAccessDecision:
        return FinanceAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> FinanceAccessDecision:
        return FinanceAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class FinanceAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: FinanceCommand,
        resource_id: UUID | None = None,
    ) -> FinanceAccessDecision: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
