"""Domain errors for the Finance service.

Two of these stand apart. ``PayoutProcedureNotDefined`` and ``ReturnFeeWaiverNotDecided``
are the two v6.3 executive Open Items that land in this context (PAY-07 and PAY-08). Both
refuse one operation and nothing else, and neither has a default that would quietly pick
an answer for an accountant.
"""

from __future__ import annotations


class FinanceError(Exception):
    """Base class for everything this context refuses."""


# ------------------------------------------------------------------ not found


class DriverAccountNotFound(FinanceError):
    def __init__(self, driver_principal_id: str) -> None:
        self.driver_principal_id = driver_principal_id
        super().__init__(f"no cash account for driver {driver_principal_id}")


class MerchantAccountNotFound(FinanceError):
    def __init__(self, merchant_id: str) -> None:
        self.merchant_id = merchant_id
        super().__init__(f"no finance account for merchant {merchant_id}")


class DepositNotFound(FinanceError):
    def __init__(self, deposit_id: str) -> None:
        self.deposit_id = deposit_id
        super().__init__(f"deposit not found: {deposit_id}")


class PayoutNotFound(FinanceError):
    def __init__(self, payout_id: str) -> None:
        self.payout_id = payout_id
        super().__init__(f"payout request not found: {payout_id}")


class ReconciliationNotFound(FinanceError):
    def __init__(self, reconciliation_id: str) -> None:
        self.reconciliation_id = reconciliation_id
        super().__init__(f"reconciliation not found: {reconciliation_id}")


class CollectionNotFound(FinanceError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"no COD collection recorded for {tracking_code}")


# ------------------------------------------------------------------ cash custody


class CashLimitExceeded(FinanceError):
    """DRV-A06 — blocks further COD assignment, never a delivery already at the door."""

    def __init__(self, held_minor_units: int, limit_minor_units: int) -> None:
        self.held_minor_units = held_minor_units
        self.limit_minor_units = limit_minor_units
        super().__init__(
            "driver is at or over their cash limit "
            f"({held_minor_units} of {limit_minor_units})"
        )


class CodAssignmentBlocked(FinanceError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"this driver may not be assigned COD: {reason}")


class MoreThanTheDriverHolds(FinanceError):
    """A driver cannot deposit cash they are not holding."""

    def __init__(self, amount_minor_units: int, held_minor_units: int) -> None:
        self.amount_minor_units = amount_minor_units
        self.held_minor_units = held_minor_units
        super().__init__(
            f"cannot deposit {amount_minor_units}; the driver holds {held_minor_units}"
        )


class CashLimitMustBePositive(FinanceError):
    def __init__(self) -> None:
        super().__init__("a cash limit of zero would stop the driver working")


# ------------------------------------------------------------------ deposits


class DepositEvidenceRequired(FinanceError):
    """DRV-A07 — amount, reference and receipt are all required."""

    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(f"a deposit needs its {missing}")


class InvalidDepositReference(FinanceError):
    def __init__(self) -> None:
        super().__init__("that is not a usable deposit reference")


class DepositAlreadyDecided(FinanceError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"this deposit is already {status}")


class OnlyAnAccountantVerifiesAnExchangeReceipt(FinanceError):
    """v6.3 p.31 — "an accountant verifies the receipt afterward"."""

    def __init__(self) -> None:
        super().__init__("only an accountant may verify an exchange receipt")


class OnlyACashierConfirmsAHubDeposit(FinanceError):
    def __init__(self) -> None:
        super().__init__("only a hub cashier may confirm a hub deposit")


class RejectionReasonRequired(FinanceError):
    def __init__(self) -> None:
        super().__init__("a rejected deposit must say why")


class HubRequiredForACashierDeposit(FinanceError):
    def __init__(self) -> None:
        super().__init__("a hub-cashier deposit must name the hub that took the cash")


# ------------------------------------------------------------------ COD


class ReceiverWalletPaymentNotOffered(FinanceError):
    """PAY-03 — "No digital wallet payment option for receivers"."""

    def __init__(self) -> None:
        super().__init__("receivers cannot pay from a wallet; v6.3 p.33 offers no such option")


class ExchangeTransferCannotConfirmPayment(FinanceError):
    """PAY-04 — it settles cash already collected; it confirms nothing."""

    def __init__(self) -> None:
        super().__init__(
            "an exchange-office transfer settles collected cash and cannot confirm "
            "the original payment"
        )


class CollectionAlreadyRecorded(FinanceError):
    def __init__(self, tracking_code: str) -> None:
        self.tracking_code = tracking_code
        super().__init__(f"COD is already recorded for {tracking_code}")


class NothingToCollect(FinanceError):
    def __init__(self) -> None:
        super().__init__("this shipment has no COD")


# ------------------------------------------------------------------ payouts


class PayoutTransitionNotAllowed(FinanceError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"a payout cannot go from {current} to {target}")


class InsufficientMerchantBalance(FinanceError):
    def __init__(self, requested_minor_units: int, available_minor_units: int) -> None:
        self.requested_minor_units = requested_minor_units
        self.available_minor_units = available_minor_units
        super().__init__(
            f"cannot pay out {requested_minor_units}; "
            f"{available_minor_units} is available"
        )


class PayoutsBlockedForThisMerchant(FinanceError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"payouts are on hold for this merchant: {reason}")


class OnlyOperationsApprovesAPayout(FinanceError):
    def __init__(self) -> None:
        super().__init__("only operations may approve or reject a payout")


class PayoutDestinationRequired(FinanceError):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"a {method} payout must name where the money goes")


class UnresolvedReconciliationBlocksPayout(FinanceError):
    """v6.3 p.31, p.33 — a mismatch is investigated **before payout**."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            f"{count} unresolved cash reconciliation(s) must be settled before a payout"
        )


# ------------------------------------------------------------------ reconciliation


class ReconciliationAlreadyResolved(FinanceError):
    def __init__(self) -> None:
        super().__init__("this reconciliation is already resolved")


class OnlyOperationsResolvesAMismatch(FinanceError):
    def __init__(self) -> None:
        super().__init__("only operations may resolve a cash mismatch")


class ResolutionNoteRequired(FinanceError):
    def __init__(self) -> None:
        super().__init__("resolving a mismatch must say what was found")


# ------------------------------------------------------------------ refunds


class RefundOnlyWhenTheReceiverPaidHudhud(FinanceError):
    """PAY-09 — otherwise it is between the merchant and the receiver."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        super().__init__(
            f"HUDHUD refunds only a receiver who paid it directly in advance; "
            f"this parcel was paid by {channel}"
        )


# --------------------------------------------------- the two Open Items (v6.3)


class PayoutProcedureNotDefined(FinanceError):
    """PAY-07 — the exact procedure per payout method needs an accountant.

    Deliberately a hard refusal of the *payment* step only. The request, its method, its
    destination, its approval and its rejection are all implemented: what cannot be done
    is move the money, because doing so means applying a fee schedule and a timing rule
    that nobody has defined.
    """

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(
            f"the operating procedure for a {method} payout is an unresolved v6.3 Open "
            "Item (Appendix A p.44) and must be defined by an accountant before a "
            "payout can be paid"
        )


class ReturnFeeWaiverNotDecided(FinanceError):
    """PAY-08 — whether HUDHUD may waive the return-trip fee is undecided.

    The charge itself is not in doubt and is implemented: v6.3 p.38 and p.39 say a
    refusal charges the merchant both fees, regardless of reason. Only the waiver is
    refused, because granting one would decide the Open Item.
    """

    def __init__(self) -> None:
        super().__init__(
            "whether the return-trip fee may be waived for a merchant is an unresolved "
            "v6.3 Open Item (Appendix A p.44); the charge stands until it is decided"
        )


class TariffNotConfigured(FinanceError):
    """No fee schedule means no charge — never a guessed amount."""

    def __init__(self, what: str) -> None:
        self.what = what
        super().__init__(f"no {what} is configured, so none can be charged")


# ------------------------------------------------------------------ concurrency


class StaleFinanceRecord(FinanceError):
    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed underneath this write")
