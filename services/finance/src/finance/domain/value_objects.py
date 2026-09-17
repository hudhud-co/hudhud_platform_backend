"""Value objects for the Finance context: accounts, postings and the states around them.

The organising idea is ADR-0012's: **every movement of money is a balanced posting.**
There is no single-sided write anywhere in this service, and a correction is another
posting rather than an edit, so the ledger can be replayed and audited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class AccountKind(StrEnum):
    """The accounts v6.3 requires, and no others.

    Each one exists because the source names a place money can be. ``EXCHANGE_IN_TRANSIT``
    is the interesting one: v6.3 p.31 and p.33 say an exchange-office transfer frees the
    driver to keep collecting *before* an accountant has verified the receipt, so the
    money has to be somewhere that is neither the driver's custody nor the bank.
    """

    #: Cash physically in a driver's hands. v6.3 p.31.
    DRIVER_CASH_CUSTODY = "DRIVER_CASH_CUSTODY"
    #: Cash handed to a hub cashier. p.33 — this is what makes a COD parcel *paid*.
    HUB_CASH = "HUB_CASH"
    #: Sent through a money-exchange office, receipt not yet verified. p.31, p.33.
    EXCHANGE_IN_TRANSIT = "EXCHANGE_IN_TRANSIT"
    #: HUDHUD's bank. Card-on-POS lands here directly; verified transfers arrive here.
    BANK = "BANK"
    #: What HUDHUD owes a merchant. The only place a merchant balance is recognised.
    MERCHANT_PAYABLE = "MERCHANT_PAYABLE"
    #: Delivery fees, return-trip fees and the rest of what HUDHUD earns.
    HUDHUD_REVENUE = "HUDHUD_REVENUE"
    #: Owed back to a receiver who paid HUDHUD directly in advance (v6.3 p.39).
    RECEIVER_REFUND_PAYABLE = "RECEIVER_REFUND_PAYABLE"


#: Accounts whose natural balance is a debit: an asset grows when money arrives in it.
DEBIT_NORMAL: frozenset[AccountKind] = frozenset(
    {
        AccountKind.DRIVER_CASH_CUSTODY,
        AccountKind.HUB_CASH,
        AccountKind.EXCHANGE_IN_TRANSIT,
        AccountKind.BANK,
    }
)

#: Accounts whose natural balance is a credit: a liability or revenue grows when
#: HUDHUD takes money it does not keep, or earns money it does.
CREDIT_NORMAL: frozenset[AccountKind] = frozenset(
    {
        AccountKind.MERCHANT_PAYABLE,
        AccountKind.HUDHUD_REVENUE,
        AccountKind.RECEIVER_REFUND_PAYABLE,
    }
)

#: Accounts that belong to one party rather than to the platform as a whole. A posting
#: to one of these must name whose it is, or a driver's custody would be everyone's.
PARTY_SCOPED: frozenset[AccountKind] = frozenset(
    {
        AccountKind.DRIVER_CASH_CUSTODY,
        AccountKind.MERCHANT_PAYABLE,
        AccountKind.RECEIVER_REFUND_PAYABLE,
    }
)


class Side(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class JournalReason(StrEnum):
    """Why a posting exists. Every one of these is named by v6.3 or by an app screen."""

    #: Cash taken at the door and now in the driver's hands (DRV-A05).
    COD_CASH_COLLECTED = "COD_CASH_COLLECTED"
    #: A POS card approval — straight to the bank, never into driver custody (DRV-L15).
    COD_CARD_COLLECTED = "COD_CARD_COLLECTED"
    #: An online payment the receiver made before delivery.
    COD_ONLINE_COLLECTED = "COD_ONLINE_COLLECTED"
    #: The receiver came to a hub and paid there (PAY-02).
    COD_PAID_AT_HUB = "COD_PAID_AT_HUB"
    #: The courier fee taken from the sender at pickup (DRV-A11, PAY-10).
    PICKUP_FEE_COLLECTED = "PICKUP_FEE_COLLECTED"
    #: Driver hands cash to a hub cashier and the cashier confirms it (DRV-A07).
    DEPOSIT_TO_HUB_CASHIER = "DEPOSIT_TO_HUB_CASHIER"
    #: Driver sends cash through an exchange office — frees the limit at once (DRV-A08).
    DEPOSIT_VIA_EXCHANGE = "DEPOSIT_VIA_EXCHANGE"
    #: An accountant verified the exchange receipt; the money reaches the bank.
    EXCHANGE_RECEIPT_VERIFIED = "EXCHANGE_RECEIPT_VERIFIED"
    #: A bank transfer the driver made directly.
    DEPOSIT_BY_BANK_TRANSFER = "DEPOSIT_BY_BANK_TRANSFER"
    #: The delivery fee HUDHUD earned on a delivered parcel.
    DELIVERY_FEE_EARNED = "DELIVERY_FEE_EARNED"
    #: PAY-08 — a refusal charges the merchant the return trip as well.
    RETURN_TRIP_FEE_CHARGED = "RETURN_TRIP_FEE_CHARGED"
    #: A payout paid out to a merchant (PAY-06).
    MERCHANT_PAYOUT = "MERCHANT_PAYOUT"
    #: A refund to a receiver who paid HUDHUD directly in advance (PAY-09).
    RECEIVER_REFUND_RECOGNISED = "RECEIVER_REFUND_RECOGNISED"
    RECEIVER_REFUND_PAID = "RECEIVER_REFUND_PAID"
    #: End-of-route reconciliation found a shortfall or a surplus (DRV-A13).
    RECONCILIATION_ADJUSTMENT = "RECONCILIATION_ADJUSTMENT"
    #: A correction for an earlier posting. Never an edit — always another posting.
    CORRECTION = "CORRECTION"


class CodPaymentChannel(StrEnum):
    """PAY-01 — the only three ways a COD parcel counts as paid, plus paying at a hub.

    v6.3 p.33 is explicit that cash counts **only once it reaches the hub cashier**, which
    is why ``CASH`` and the deposit that settles it are separate events here.
    """

    ONLINE = "ONLINE"
    POS_CARD = "POS_CARD"
    CASH = "CASH"
    AT_HUB_IN_PERSON = "AT_HUB_IN_PERSON"


#: PAY-03 — "No digital wallet payment option for receivers." Recorded as an explicit
#: absence so that adding one is a deliberate change to this line, not a quiet new enum
#: member. The test suite asserts the channel set equals exactly the four above.
RECEIVER_WALLET_PAYMENT_OFFERED = False


class DepositMethod(StrEnum):
    """DRV-A07 — the three ways a driver gets cash out of their own custody."""

    HUB_CASHIER = "HUB_CASHIER"
    EXCHANGE_OFFICE = "EXCHANGE_OFFICE"
    BANK_TRANSFER = "BANK_TRANSFER"


class DepositStatus(StrEnum):
    """DRV-A08, DRV-A09 — pending frees the limit; only verification settles it.

    ``PENDING_VERIFICATION`` is not a formality: v6.3 p.31 lets the driver keep collecting
    the moment the exchange transfer is made, and p.33 says the transfer is not a way to
    confirm the original payment. Both are true at once only because the money sits in
    ``EXCHANGE_IN_TRANSIT`` in between.
    """

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


DEPOSIT_TRANSITIONS: dict[DepositStatus, frozenset[DepositStatus]] = {
    DepositStatus.PENDING_VERIFICATION: frozenset(
        {DepositStatus.CONFIRMED, DepositStatus.REJECTED}
    ),
    DepositStatus.CONFIRMED: frozenset(),
    DepositStatus.REJECTED: frozenset(),
}


class PayoutMethod(StrEnum):
    """PAY-06 — the four methods v6.3 p.31 and p.34 name, and no others."""

    BANK_TRANSFER = "BANK_TRANSFER"
    MASTERCARD = "MASTERCARD"
    MONEY_EXCHANGE = "MONEY_EXCHANGE"
    IN_PERSON_AT_HUB = "IN_PERSON_AT_HUB"


class PayoutStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    PAID = "PAID"
    REJECTED = "REJECTED"


PAYOUT_TRANSITIONS: dict[PayoutStatus, frozenset[PayoutStatus]] = {
    PayoutStatus.REQUESTED: frozenset({PayoutStatus.APPROVED, PayoutStatus.REJECTED}),
    PayoutStatus.APPROVED: frozenset({PayoutStatus.PAID, PayoutStatus.REJECTED}),
    PayoutStatus.PAID: frozenset(),
    PayoutStatus.REJECTED: frozenset(),
}


class ReconciliationOutcome(StrEnum):
    """DRV-A13 — what the end-of-route count found."""

    BALANCED = "BALANCED"
    SHORT = "SHORT"
    OVER = "OVER"


class ReconciliationStatus(StrEnum):
    """A mismatch is investigated **before payout** (v6.3 p.31, p.33)."""

    OPEN = "OPEN"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True, slots=True)
class AccountRef:
    """Which account, and whose.

    A party-scoped account without a party is meaningless — "driver cash custody" is not
    a place, one driver's custody is — so the pairing is checked here rather than trusted.
    """

    kind: AccountKind
    party_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind in PARTY_SCOPED and self.party_id is None:
            msg = f"{self.kind.value} must name the party it belongs to"
            raise ValueError(msg)
        if self.kind not in PARTY_SCOPED and self.party_id is not None:
            msg = f"{self.kind.value} is a platform account and names no party"
            raise ValueError(msg)

    @property
    def is_debit_normal(self) -> bool:
        return self.kind in DEBIT_NORMAL


@dataclass(frozen=True, slots=True)
class EvidenceMediaRef:
    """A pointer to a receipt image. Finance never holds the bytes."""

    bucket: str
    key: str
    content_type: str | None = None


_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{2,63}$")


def is_valid_reference(value: str) -> bool:
    """A deposit or transaction reference as printed on a receipt."""
    return bool(_REFERENCE.match(value))
