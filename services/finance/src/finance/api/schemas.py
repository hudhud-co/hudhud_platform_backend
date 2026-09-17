"""Request and response models for the Finance HTTP adapter.

Money crosses this boundary as an integer count of minor units plus an explicit
currency — never a decimal, never a formatted string. A JSON number that looks like
`105000.00` has already been through a float somewhere, and this is the layer where that
would get in.

What never leaves: a payout destination belongs to the merchant who gave it, and a
journal entry's internal memo can name a person or an investigation.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from finance.domain.value_objects import (
    AccountKind,
    CodPaymentChannel,
    DepositMethod,
    DepositStatus,
    JournalReason,
    PayoutMethod,
    PayoutStatus,
    ReconciliationOutcome,
    ReconciliationStatus,
    Side,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoneyModel(_Model):
    """Exact IQD. ``minor_units`` is an integer and pydantic will refuse a float."""

    minor_units: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class MediaRefModel(_Model):
    bucket: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=512)
    content_type: str | None = Field(default=None, max_length=128)


# ------------------------------------------------------------------ the ledger


class PostingResponse(_Model):
    account_kind: AccountKind
    party_id: UUID | None
    side: Side
    amount: MoneyModel


class JournalEntryResponse(_Model):
    """A posted entry. Read-only by construction: there is no request model for one."""

    entry_id: UUID
    reason: JournalReason
    occurred_at: datetime
    total: MoneyModel
    postings: list[PostingResponse]
    subject_kind: str | None
    subject_id: UUID | None
    corrects_entry_id: UUID | None


class BalanceResponse(_Model):
    account_kind: AccountKind
    party_id: UUID | None
    balance: MoneyModel
    posting_count: int


# ------------------------------------------------------------------ COD


class RecordCollectionRequest(_Model):
    """PAY-01 — what was taken for one parcel, split into goods and fee.

    The split is required rather than inferred: the goods are the merchant's money and
    the fee is HUDHUD's, and a single total would overstate what the merchant is owed by
    exactly the fee, every time.
    """

    tracking_code: str = Field(min_length=1, max_length=32)
    merchant_id: UUID
    channel: CodPaymentChannel
    goods_amount: MoneyModel
    delivery_fee: MoneyModel
    driver_principal_id: UUID | None = None
    #: A client key, so a retried request finds its entry instead of posting a second.
    idempotency_key: str | None = Field(default=None, max_length=128)


class CollectionResponse(_Model):
    collection_id: UUID
    tracking_code: str
    merchant_id: UUID
    channel: CodPaymentChannel
    goods_amount: MoneyModel
    delivery_fee: MoneyModel
    total: MoneyModel
    collected_at: datetime
    driver_principal_id: UUID | None
    is_paid: bool
    settled_at: datetime | None
    settling_deposit_id: UUID | None
    journal_entry_id: UUID | None


class RecordPickupFeeRequest(_Model):
    """DRV-A11, PAY-10 — the courier fee the sender pays at pickup."""

    tracking_code: str = Field(min_length=1, max_length=32)
    merchant_id: UUID
    amount: MoneyModel
    by_card: bool = False


class RecogniseRefundRequest(_Model):
    """PAY-09 — only where the receiver paid HUDHUD directly in advance."""

    receiver_principal_id: UUID
    amount: MoneyModel


# ------------------------------------------------------------------ cash custody


class OpenCashAccountRequest(_Model):
    driver_principal_id: UUID
    #: Omitted means the configured platform default (DRV-A06).
    limit: MoneyModel | None = None


class SetCashLimitRequest(_Model):
    limit: MoneyModel


class BlockCodRequest(_Model):
    blocked: bool = True
    reason: str = Field(min_length=1, max_length=256)


class CashPositionResponse(_Model):
    """DRV-A05, DRV-A06 — what the driver holds and what they may still take."""

    driver_principal_id: UUID
    held: MoneyModel
    limit: MoneyModel
    headroom: MoneyModel
    utilisation_percent: int
    over_limit: bool
    may_take_more_cod: bool
    open_deposits: int


class CashExposureResponse(_Model):
    """OPS-04 — one row per driver, worst first."""

    driver_principal_id: UUID
    held: MoneyModel
    limit: MoneyModel
    utilisation_percent: int
    over_limit: bool
    open_deposits: int


# ------------------------------------------------------------------ deposits


class SubmitDepositRequest(_Model):
    """DRV-A07 — amount, reference and receipt, all three, for every method."""

    method: DepositMethod
    amount: MoneyModel
    reference: str = Field(min_length=3, max_length=64)
    receipt: MediaRefModel
    #: Required for a hub-cashier deposit: which hub took the cash.
    hub_id: UUID | None = None


class RejectDepositRequest(_Model):
    reason: str = Field(min_length=1, max_length=512)


class DepositResponse(_Model):
    deposit_id: UUID
    driver_principal_id: UUID
    method: DepositMethod
    amount: MoneyModel
    reference: str
    status: DepositStatus
    submitted_at: datetime | None
    hub_id: UUID | None
    decided_at: datetime | None
    rejection_reason: str | None
    #: PAY-04 — true only for a hub-cashier deposit; an exchange transfer settles cash
    #: already collected and confirms no original payment.
    settles_the_parcels_it_covers: bool


class SubmitDepositResponse(_Model):
    deposit: DepositResponse
    #: What the driver holds once the cash left their hands (DRV-A09).
    held_after: MoneyModel


class SettlementHistoryItem(_Model):
    """DRV-A10 — one settlement, with the receipt it was made against."""

    deposit_id: UUID
    method: DepositMethod
    amount: MoneyModel
    status: DepositStatus
    submitted_at: datetime | None
    decided_at: datetime | None
    reference: str
    receipt: MediaRefModel


# ------------------------------------------------------------------ merchant


class MerchantBalanceResponse(_Model):
    """PAY-05 — derived from the ledger, never a stored running total."""

    merchant_id: UUID
    balance: MoneyModel
    available: MoneyModel
    committed: MoneyModel
    payouts_blocked: bool
    payouts_blocked_reason: str | None
    open_payout_count: int


class RequestPayoutRequest(_Model):
    """PAY-06 — the four methods v6.3 names, and no others."""

    method: PayoutMethod
    amount: MoneyModel
    #: Required for every method except collecting in person.
    destination_reference: str | None = Field(default=None, max_length=128)


class RejectPayoutRequest(_Model):
    reason: str = Field(min_length=1, max_length=512)


class PayoutResponse(_Model):
    payout_id: UUID
    merchant_id: UUID
    method: PayoutMethod
    amount: MoneyModel
    status: PayoutStatus
    #: Whether one was given, never what it is.
    has_destination: bool
    requested_at: datetime | None
    decided_at: datetime | None
    rejection_reason: str | None
    paid_at: datetime | None


# ------------------------------------------------------------------ PAY-08


class ChargeRefusalRequest(_Model):
    """v6.3 p.38, p.39 — both fees, regardless of reason.

    There is deliberately no ``reason`` field: "regardless of reason" means no reason
    changes the answer, so accepting one would imply there might be.
    """

    tracking_code: str = Field(min_length=1, max_length=32)
    merchant_id: UUID
    delivery_fee: MoneyModel


class RefusalChargeResponse(_Model):
    tracking_code: str
    merchant_id: UUID
    delivery_fee: MoneyModel
    return_trip_fee: MoneyModel
    total: MoneyModel


class WaiveReturnFeeRequest(_Model):
    """PAY-08's Open Item. Answers 501 until an accountant decides."""

    tracking_code: str = Field(min_length=1, max_length=32)
    merchant_id: UUID
    amount: MoneyModel
    note: str = Field(min_length=1, max_length=512)


# ------------------------------------------------------------------ reconciliation


class OpenReconciliationRequest(_Model):
    """DRV-A13 — the count only. What was expected comes from the ledger.

    A caller-supplied expectation alongside the count would let the two agree by
    construction, which is the one thing a reconciliation must not do.
    """

    driver_principal_id: UUID
    route_day: date
    counted: MoneyModel
    parcels_returned_confirmed: bool = False


class ConfirmHubReturnRequest(_Model):
    """DRV-A12 — both halves of the end-of-day return."""

    parcels: bool = True
    cash: bool = True


class ResolveReconciliationRequest(_Model):
    note: str = Field(min_length=1, max_length=2000)
    post_adjustment: bool = True


class ReconciliationResponse(_Model):
    reconciliation_id: UUID
    driver_principal_id: UUID
    route_day: date
    expected: MoneyModel
    counted: MoneyModel
    difference: MoneyModel
    outcome: ReconciliationOutcome
    status: ReconciliationStatus
    parcels_returned_confirmed: bool
    cash_settled_confirmed: bool
    day_is_complete: bool
    blocks_payout: bool
    resolution_note: str | None
    adjustment_entry_id: UUID | None


class OpenItemsResponse(_Model):
    """What this service will not do, and why — so an operator can see it.

    PAY-07 and PAY-08 are v6.3 Appendix A Open Items. Reporting them beats letting an
    operator discover a 501 and guess.
    """

    payout_payment_available: bool
    payout_payment_blocked_by: str
    return_trip_fee_configured: bool
    return_fee_waiver_permitted: bool
    return_fee_waiver_blocked_by: str
