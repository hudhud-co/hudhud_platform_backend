"""Domain error to HTTP mapping for the Finance service.

Two mappings carry a business decision rather than a convention.

``PayoutProcedureNotDefined`` (PAY-07) and ``ReturnFeeWaiverNotDecided`` (PAY-08) answer
**501**, not 400 and not 503. The request is well formed, the service is healthy, and the
operation is simply not implementable until an accountant decides. A 400 would blame the
caller and a 503 would invite a retry that can never succeed.

``TariffNotConfigured`` answers **503** instead, because that one *is* a deployment
problem: the return-trip fee is a number somebody has to set, and setting it fixes it.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from finance.domain.errors import (
    CashLimitExceeded,
    CashLimitMustBePositive,
    CodAssignmentBlocked,
    CollectionAlreadyRecorded,
    CollectionNotFound,
    DepositAlreadyDecided,
    DepositEvidenceRequired,
    DepositNotFound,
    DriverAccountNotFound,
    ExchangeTransferCannotConfirmPayment,
    FinanceError,
    HubRequiredForACashierDeposit,
    InsufficientMerchantBalance,
    InvalidDepositReference,
    MerchantAccountNotFound,
    MoreThanTheDriverHolds,
    NothingToCollect,
    OnlyACashierConfirmsAHubDeposit,
    OnlyAnAccountantVerifiesAnExchangeReceipt,
    OnlyOperationsApprovesAPayout,
    OnlyOperationsResolvesAMismatch,
    PayoutDestinationRequired,
    PayoutNotFound,
    PayoutProcedureNotDefined,
    PayoutsBlockedForThisMerchant,
    PayoutTransitionNotAllowed,
    ReceiverWalletPaymentNotOffered,
    ReconciliationAlreadyResolved,
    ReconciliationNotFound,
    RefundOnlyWhenTheReceiverPaidHudhud,
    RejectionReasonRequired,
    ResolutionNoteRequired,
    ReturnFeeWaiverNotDecided,
    StaleFinanceRecord,
    TariffNotConfigured,
    UnresolvedReconciliationBlocksPayout,
)
from finance.domain.ledger import (
    EmptyEntry,
    LedgerError,
    MixedCurrencyEntry,
    SingleSidedEntry,
    UnbalancedEntry,
    ZeroPosting,
)

_STATUS: dict[type[Exception], int] = {
    DriverAccountNotFound: 404,
    MerchantAccountNotFound: 404,
    DepositNotFound: 404,
    PayoutNotFound: 404,
    ReconciliationNotFound: 404,
    CollectionNotFound: 404,
    # 409: the request is well formed and the state says no.
    CashLimitExceeded: 409,
    CodAssignmentBlocked: 409,
    MoreThanTheDriverHolds: 409,
    DepositAlreadyDecided: 409,
    CollectionAlreadyRecorded: 409,
    NothingToCollect: 409,
    PayoutTransitionNotAllowed: 409,
    InsufficientMerchantBalance: 409,
    PayoutsBlockedForThisMerchant: 409,
    UnresolvedReconciliationBlocksPayout: 409,
    ReconciliationAlreadyResolved: 409,
    StaleFinanceRecord: 409,
    # 422: the request itself is missing or malformed.
    CashLimitMustBePositive: 422,
    DepositEvidenceRequired: 422,
    InvalidDepositReference: 422,
    HubRequiredForACashierDeposit: 422,
    RejectionReasonRequired: 422,
    ResolutionNoteRequired: 422,
    PayoutDestinationRequired: 422,
    # 403: this actor is the wrong one. ADR-0012 separates each of these on purpose.
    OnlyAnAccountantVerifiesAnExchangeReceipt: 403,
    OnlyACashierConfirmsAHubDeposit: 403,
    OnlyOperationsApprovesAPayout: 403,
    OnlyOperationsResolvesAMismatch: 403,
    # 409: v6.3 says these are not things HUDHUD does.
    ReceiverWalletPaymentNotOffered: 409,
    ExchangeTransferCannotConfirmPayment: 409,
    RefundOnlyWhenTheReceiverPaidHudhud: 409,
    # 503: a number nobody set. Setting it fixes this.
    TariffNotConfigured: 503,
    # 501: a decision nobody made. See this module's docstring.
    PayoutProcedureNotDefined: 501,
    ReturnFeeWaiverNotDecided: 501,
    # 500: the ledger refused its own invariant. This is a bug in Finance, not a client
    # error, and it must be loud.
    UnbalancedEntry: 500,
    EmptyEntry: 500,
    SingleSidedEntry: 500,
    MixedCurrencyEntry: 500,
    ZeroPosting: 500,
}

_CODE: dict[type[Exception], str] = {
    DriverAccountNotFound: "driver_account_not_found",
    MerchantAccountNotFound: "merchant_account_not_found",
    DepositNotFound: "deposit_not_found",
    PayoutNotFound: "payout_not_found",
    ReconciliationNotFound: "reconciliation_not_found",
    CollectionNotFound: "collection_not_found",
    CashLimitExceeded: "cash_limit_exceeded",
    CodAssignmentBlocked: "cod_assignment_blocked",
    MoreThanTheDriverHolds: "more_than_the_driver_holds",
    DepositAlreadyDecided: "deposit_already_decided",
    CollectionAlreadyRecorded: "collection_already_recorded",
    NothingToCollect: "nothing_to_collect",
    PayoutTransitionNotAllowed: "payout_transition_not_allowed",
    InsufficientMerchantBalance: "insufficient_merchant_balance",
    PayoutsBlockedForThisMerchant: "payouts_blocked_for_this_merchant",
    UnresolvedReconciliationBlocksPayout: "unresolved_reconciliation_blocks_payout",
    ReconciliationAlreadyResolved: "reconciliation_already_resolved",
    StaleFinanceRecord: "stale_finance_record",
    CashLimitMustBePositive: "cash_limit_must_be_positive",
    DepositEvidenceRequired: "deposit_evidence_required",
    InvalidDepositReference: "invalid_deposit_reference",
    HubRequiredForACashierDeposit: "hub_required_for_a_cashier_deposit",
    RejectionReasonRequired: "rejection_reason_required",
    ResolutionNoteRequired: "resolution_note_required",
    PayoutDestinationRequired: "payout_destination_required",
    OnlyAnAccountantVerifiesAnExchangeReceipt: (
        "only_an_accountant_verifies_an_exchange_receipt"
    ),
    OnlyACashierConfirmsAHubDeposit: "only_a_cashier_confirms_a_hub_deposit",
    OnlyOperationsApprovesAPayout: "only_operations_approves_a_payout",
    OnlyOperationsResolvesAMismatch: "only_operations_resolves_a_mismatch",
    ReceiverWalletPaymentNotOffered: "receiver_wallet_payment_not_offered",
    ExchangeTransferCannotConfirmPayment: "exchange_transfer_cannot_confirm_payment",
    RefundOnlyWhenTheReceiverPaidHudhud: "refund_only_when_the_receiver_paid_hudhud",
    TariffNotConfigured: "tariff_not_configured",
    PayoutProcedureNotDefined: "payout_procedure_not_defined",
    ReturnFeeWaiverNotDecided: "return_fee_waiver_not_decided",
    UnbalancedEntry: "ledger_invariant_violated",
    EmptyEntry: "ledger_invariant_violated",
    SingleSidedEntry: "ledger_invariant_violated",
    MixedCurrencyEntry: "ledger_invariant_violated",
    ZeroPosting: "ledger_invariant_violated",
}


def raise_http_for_domain_error(error: Exception) -> NoReturn:
    """Translate a domain error into its HTTP answer.

    The detail carries a stable machine code and nothing else. An exception message here
    can contain an amount, a merchant's balance or a reference from a receipt, and this
    is the one place a careless f-string would put it in a client response and a log.
    """
    status = _STATUS.get(type(error))
    if status is None:
        if isinstance(error, LedgerError):
            status = 500
        elif isinstance(error, FinanceError):
            status = 409
        else:
            raise error
    code = _CODE.get(type(error), "finance_error")
    raise HTTPException(status_code=status, detail={"code": code})
