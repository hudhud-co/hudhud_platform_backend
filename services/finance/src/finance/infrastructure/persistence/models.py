"""SQLAlchemy models for the Finance-owned database.

The ledger tables are the ones to read carefully. ``finance_journal_entries`` and
``finance_journal_postings`` are **append-only**: nothing in this service issues an
UPDATE or a DELETE against either, a mistake is corrected by posting a reversing entry
(ADR-0012), and the database refuses to hold an entry whose postings do not balance —
enforced with a deferred constraint trigger in the migration, because a balance is a
property of a set of rows rather than of any one row.

Money is integer minor units with an explicit currency. There is no NUMERIC and no float
on any column in this service, monetary or otherwise.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JournalEntryRow(Base):
    """One balanced movement of money. Append-only.

    ``idempotency_key`` is unique where present: a retried command finds the entry it
    already made instead of posting a second one, which for money is the difference
    between a network hiccup and a double charge.
    """

    __tablename__ = "finance_journal_entries"
    __table_args__ = (
        UniqueConstraint("entry_id", name="uq_finance_entry_id"),
        Index(
            "uq_finance_entry_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
        Index("ix_finance_entry_occurred_at", "occurred_at"),
        Index("ix_finance_entry_subject", "subject_kind", "subject_id"),
        Index(
            "ix_finance_entry_corrects",
            "corrects_entry_id",
            postgresql_where="corrects_entry_id IS NOT NULL",
        ),
        # A correcting entry says so in both fields or in neither.
        CheckConstraint(
            "(corrects_entry_id IS NULL) OR (reason = 'CORRECTION')",
            name="ck_finance_entry_correction_is_labelled",
        ),
        CheckConstraint(
            "total_minor_units > 0", name="ck_finance_entry_total_is_positive"
        ),
    )

    entry_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    #: One side of the entry, since both sides are equal. Stored so a reader does not
    #: have to sum the postings to know how big the entry was.
    total_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recorded_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    subject_kind: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    corrects_entry_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    memo: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class JournalPostingRow(Base):
    """One side of one entry. Append-only, like the entry that holds it.

    There is no ``version`` column here on purpose: a version exists so a row can be
    safely *changed*, and a posting never is.
    """

    __tablename__ = "finance_journal_postings"
    __table_args__ = (
        Index("ix_finance_posting_entry", "entry_id"),
        # The query behind every balance: one account's postings, in order.
        Index(
            "ix_finance_posting_account",
            "account_kind",
            "party_id",
            "occurred_at",
        ),
        CheckConstraint("side IN ('DEBIT', 'CREDIT')", name="ck_finance_posting_side"),
        CheckConstraint(
            "amount_minor_units > 0", name="ck_finance_posting_amount_is_positive"
        ),
        # A party-scoped account names its party; a platform account does not. "Driver
        # cash custody" is not a place — one driver's custody is.
        CheckConstraint(
            "(account_kind IN ("
            "'DRIVER_CASH_CUSTODY', 'MERCHANT_PAYABLE', 'RECEIVER_REFUND_PAYABLE'"
            ")) = (party_id IS NOT NULL)",
            name="ck_finance_posting_party_matches_account_kind",
        ),
    )

    posting_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    entry_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    account_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    party_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    side: Mapped[str] = mapped_column(String(6), nullable=False)
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    #: Denormalised from the entry so an account's postings can be read and ordered
    #: without joining. A balance query is the hottest read in this service.
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Sequence within the entry, so the two sides of a movement keep their order.
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class DriverCashAccountRow(Base):
    """A driver's limit and whether they may take COD (DRV-A05, DRV-A06).

    What they *hold* is not here: it is derived from the postings, so it cannot drift
    from the entries that are meant to explain it.
    """

    __tablename__ = "finance_driver_cash_accounts"
    __table_args__ = (
        UniqueConstraint(
            "driver_principal_id", name="uq_finance_driver_cash_account_principal"
        ),
        CheckConstraint(
            "limit_minor_units > 0", name="ck_finance_cash_limit_is_positive"
        ),
        CheckConstraint(
            "cod_blocked = false OR cod_blocked_reason IS NOT NULL",
            name="ck_finance_cod_block_has_a_reason",
        ),
    )

    account_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    limit_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    limit_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cod_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    cod_blocked_reason: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MerchantAccountRow(Base):
    """A merchant's settlement policy (PAY-05). The balance itself is the ledger's."""

    __tablename__ = "finance_merchant_accounts"
    __table_args__ = (
        UniqueConstraint("merchant_id", name="uq_finance_merchant_account"),
        CheckConstraint(
            "payouts_blocked = false OR payouts_blocked_reason IS NOT NULL",
            name="ck_finance_payout_block_has_a_reason",
        ),
    )

    account_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    payouts_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    payouts_blocked_reason: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CodCollectionRow(Base):
    """One parcel's COD (PAY-01).

    The constraint below is the whole of v6.3 p.33 in one line: cash is settled only by
    a deposit, and everything else is settled the moment it is taken.
    """

    __tablename__ = "finance_cod_collections"
    __table_args__ = (
        UniqueConstraint("tracking_code", name="uq_finance_collection_tracking_code"),
        Index("ix_finance_collection_merchant", "merchant_id", "collected_at"),
        Index(
            "ix_finance_collection_unsettled_driver",
            "driver_principal_id",
            "collected_at",
            postgresql_where="settled_at IS NULL",
        ),
        CheckConstraint(
            "goods_minor_units >= 0 AND delivery_fee_minor_units >= 0",
            name="ck_finance_collection_amounts_not_negative",
        ),
        CheckConstraint(
            "goods_minor_units + delivery_fee_minor_units > 0",
            name="ck_finance_collection_collects_something",
        ),
        # PAY-01, PAY-04 — cash becomes paid only through a hub-cashier deposit, which
        # is why a settled cash collection always names one.
        CheckConstraint(
            "channel <> 'CASH' OR settled_at IS NULL OR settling_deposit_id IS NOT NULL",
            name="ck_finance_settled_cash_names_its_deposit",
        ),
        # Cash is the only channel that arrives in a person's hands.
        CheckConstraint(
            "channel <> 'CASH' OR driver_principal_id IS NOT NULL",
            name="ck_finance_cash_collection_names_the_driver",
        ),
        CheckConstraint(
            "(settling_deposit_id IS NULL) OR (settled_at IS NOT NULL)",
            name="ck_finance_settling_deposit_implies_settled",
        ),
    )

    collection_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tracking_code: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    goods_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_fee_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    collected_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    driver_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    settled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    settling_deposit_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    journal_entry_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DepositRow(Base):
    """A driver getting cash out of custody (DRV-A07 … DRV-A10)."""

    __tablename__ = "finance_deposits"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_finance_deposit_reference"),
        Index("ix_finance_deposit_driver", "driver_principal_id", "submitted_at"),
        Index(
            "ix_finance_deposit_pending",
            "submitted_at",
            postgresql_where="status = 'PENDING_VERIFICATION'",
        ),
        CheckConstraint(
            "amount_minor_units > 0", name="ck_finance_deposit_amount_is_positive"
        ),
        # DRV-A07 — amount, reference and receipt, all three, for every method.
        CheckConstraint(
            "receipt_bucket IS NOT NULL AND receipt_key IS NOT NULL",
            name="ck_finance_deposit_has_a_receipt",
        ),
        CheckConstraint(
            "method <> 'HUB_CASHIER' OR hub_id IS NOT NULL",
            name="ck_finance_hub_deposit_names_the_hub",
        ),
        # Every decision names who made it and when. The person who handed the money
        # over is never the one who says it arrived.
        CheckConstraint(
            "status = 'PENDING_VERIFICATION' OR ("
            "decided_at IS NOT NULL AND decided_by_actor_id IS NOT NULL)",
            name="ck_finance_deposit_decision_is_attributed",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_finance_deposit_rejection_has_a_reason",
        ),
    )

    deposit_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_key: Mapped[str] = mapped_column(String(512), nullable=False)
    receipt_content_type: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    submitted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    hub_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(512))
    submitted_entry_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    settled_entry_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PayoutRequestRow(Base):
    """A merchant asking to be paid (PAY-06).

    ``paid_at`` and ``paid_entry_id`` exist and are always null today: PAY-07 blocks the
    payment step. They are here rather than added later so the day an accountant defines
    the procedure is a code change and not a migration on a live financial table.
    """

    __tablename__ = "finance_payout_requests"
    __table_args__ = (
        Index("ix_finance_payout_merchant", "merchant_id", "requested_at"),
        Index(
            "ix_finance_payout_open",
            "requested_at",
            postgresql_where="status IN ('REQUESTED', 'APPROVED')",
        ),
        CheckConstraint(
            "amount_minor_units > 0", name="ck_finance_payout_amount_is_positive"
        ),
        # PAY-06 — collecting in person needs no destination; the other three do, or
        # nobody knows where to send it.
        CheckConstraint(
            "method = 'IN_PERSON_AT_HUB' OR destination_reference IS NOT NULL",
            name="ck_finance_payout_names_its_destination",
        ),
        CheckConstraint(
            "status = 'REQUESTED' OR ("
            "decided_at IS NOT NULL AND decided_by_actor_id IS NOT NULL)",
            name="ck_finance_payout_decision_is_attributed",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_finance_payout_rejection_has_a_reason",
        ),
        CheckConstraint(
            "(status = 'PAID') = (paid_at IS NOT NULL)",
            name="ck_finance_payout_paid_at_matches_status",
        ),
    )

    payout_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_reference: Mapped[str | None] = mapped_column(String(128))
    requested_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(512))
    paid_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    paid_entry_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RouteReconciliationRow(Base):
    """The end-of-route count (DRV-A12, DRV-A13)."""

    __tablename__ = "finance_route_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "driver_principal_id",
            "route_day",
            name="uq_finance_reconciliation_driver_day",
        ),
        Index(
            "ix_finance_reconciliation_unresolved",
            "opened_at",
            postgresql_where="status <> 'RESOLVED'",
        ),
        CheckConstraint(
            "expected_minor_units >= 0 AND counted_minor_units >= 0",
            name="ck_finance_reconciliation_amounts_not_negative",
        ),
        # The outcome is derived from the two figures, so it must agree with them.
        CheckConstraint(
            "(outcome = 'BALANCED' AND counted_minor_units = expected_minor_units) OR "
            "(outcome = 'SHORT' AND counted_minor_units < expected_minor_units) OR "
            "(outcome = 'OVER' AND counted_minor_units > expected_minor_units)",
            name="ck_finance_reconciliation_outcome_matches_the_count",
        ),
        CheckConstraint(
            "status <> 'RESOLVED' OR resolved_at IS NOT NULL",
            name="ck_finance_reconciliation_resolved_has_a_time",
        ),
        # A mismatch somebody closed always says who and what they found.
        CheckConstraint(
            "outcome = 'BALANCED' OR status <> 'RESOLVED' OR ("
            "resolved_by_actor_id IS NOT NULL AND resolution_note IS NOT NULL)",
            name="ck_finance_mismatch_resolution_is_attributed",
        ),
    )

    reconciliation_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    driver_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    route_day: Mapped[object] = mapped_column(Date, nullable=False)
    expected_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counted_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    parcels_returned_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    cash_settled_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    opened_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    resolved_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    adjustment_entry_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IntegrationOutboxRow(Base):
    """Finance-owned transactional integration outbox (ADR-0008)."""

    __tablename__ = "finance_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_finance_outbox_event_id"),
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_finance_outbox_aggregate_version",
        ),
        Index(
            "ix_finance_outbox_pending",
            "status",
            "next_attempt_at",
            postgresql_where="status IN ('pending', 'processing')",
        ),
    )

    id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processing_owner: Mapped[str | None] = mapped_column(String(128))
    processing_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationInboxRow(Base):
    """Finance-owned durable inbox (ADR-0008).

    The reason this table matters more here than anywhere else: a redelivered
    ``delivery.fact.cod_collected`` that ran twice would post the money twice.
    """

    __tablename__ = "finance_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_finance_inbox_consumer_event"
        ),
        Index("ix_finance_inbox_status", "status", "processing_lease_until"),
    )

    inbox_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    processing_lease_until: Mapped[object | None] = mapped_column(
        DateTime(timezone=True)
    )
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
