"""W27: Finance core — double-entry ledger, COD, driver cash custody, payouts.

Expand-only: this migration creates new tables and touches nothing that exists.

Three things here are enforced by the database rather than by the service, because each
is a rule that a single direct statement could otherwise break, and this is money.

1. **The ledger is append-only.** ``finance_journal_entries`` and
   ``finance_journal_postings`` carry triggers that raise on UPDATE and on DELETE. A
   mistaken entry is corrected by posting its reverse (ADR-0012); there is no other way,
   and now there is no other way even with a psql prompt.

2. **Every entry balances.** A balance is a property of a set of rows, so it cannot be a
   CHECK constraint. It is a *deferred* constraint trigger instead: the postings are
   inserted, and at COMMIT the trigger sums them and rejects the transaction if the
   debits and credits differ, if either side is missing, or if the currencies disagree.
   Deferring it is what lets a normal two-statement insert work at all.

3. **A posting's account and party agree.** A party-scoped account names its party and a
   platform account does not — "driver cash custody" is not a place, one driver's
   custody is.

Money is integer minor units with an explicit currency throughout. There is no NUMERIC
and no float on any column in this service.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w27_finance_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_APPEND_ONLY_GUARD = """
CREATE OR REPLACE FUNCTION finance_refuse_ledger_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'the finance ledger is append-only: % on % is refused. Correct a mistaken '
        'entry by posting its reverse (ADR-0012).',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

_BALANCE_GUARD = """
CREATE OR REPLACE FUNCTION finance_assert_entry_balances()
RETURNS trigger AS $$
DECLARE
    v_entry uuid;
    v_debits bigint;
    v_credits bigint;
    v_postings integer;
    v_currencies integer;
BEGIN
    v_entry := NEW.entry_id;

    SELECT
        COALESCE(SUM(amount_minor_units) FILTER (WHERE side = 'DEBIT'), 0),
        COALESCE(SUM(amount_minor_units) FILTER (WHERE side = 'CREDIT'), 0),
        COUNT(*),
        COUNT(DISTINCT currency)
    INTO v_debits, v_credits, v_postings, v_currencies
    FROM finance_journal_postings
    WHERE entry_id = v_entry;

    -- The entry may have been rolled back before COMMIT; nothing to check.
    IF v_postings = 0 THEN
        RETURN NULL;
    END IF;

    IF v_postings < 2 THEN
        RAISE EXCEPTION 'journal entry % has % posting(s); an entry needs at least two',
            v_entry, v_postings
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_currencies > 1 THEN
        RAISE EXCEPTION 'journal entry % mixes % currencies; one entry, one currency',
            v_entry, v_currencies
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_debits = 0 OR v_credits = 0 THEN
        RAISE EXCEPTION 'journal entry % is single-sided (debits %, credits %)',
            v_entry, v_debits, v_credits
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_debits <> v_credits THEN
        RAISE EXCEPTION
            'journal entry % does not balance: debits % <> credits %',
            v_entry, v_debits, v_credits
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # ------------------------------------------------------------- the ledger
    op.create_table(
        "finance_journal_entries",
        sa.Column("entry_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("reason", sa.String(length=48), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("recorded_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("subject_kind", sa.String(length=32), nullable=True),
        sa.Column("subject_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("corrects_entry_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entry_id", name="uq_finance_entry_id"),
        sa.CheckConstraint(
            "(corrects_entry_id IS NULL) OR (reason = 'CORRECTION')",
            name="ck_finance_entry_correction_is_labelled",
        ),
        sa.CheckConstraint(
            "total_minor_units > 0", name="ck_finance_entry_total_is_positive"
        ),
    )
    # A retried command must find the entry it already made, not post a second one.
    op.create_index(
        "uq_finance_entry_idempotency_key",
        "finance_journal_entries",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_finance_entry_occurred_at", "finance_journal_entries", ["occurred_at"]
    )
    op.create_index(
        "ix_finance_entry_subject",
        "finance_journal_entries",
        ["subject_kind", "subject_id"],
    )
    op.create_index(
        "ix_finance_entry_corrects",
        "finance_journal_entries",
        ["corrects_entry_id"],
        postgresql_where=sa.text("corrects_entry_id IS NOT NULL"),
    )

    op.create_table(
        "finance_journal_postings",
        sa.Column("posting_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("entry_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_kind", sa.String(length=32), nullable=False),
        sa.Column("party_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("side", sa.String(length=6), nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("side IN ('DEBIT', 'CREDIT')", name="ck_finance_posting_side"),
        sa.CheckConstraint(
            "amount_minor_units > 0", name="ck_finance_posting_amount_is_positive"
        ),
        sa.CheckConstraint(
            "(account_kind IN ("
            "'DRIVER_CASH_CUSTODY', 'MERCHANT_PAYABLE', 'RECEIVER_REFUND_PAYABLE'"
            ")) = (party_id IS NOT NULL)",
            name="ck_finance_posting_party_matches_account_kind",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["finance_journal_entries.entry_id"],
            name="fk_finance_posting_entry",
        ),
    )
    op.create_index("ix_finance_posting_entry", "finance_journal_postings", ["entry_id"])
    op.create_index(
        "ix_finance_posting_account",
        "finance_journal_postings",
        ["account_kind", "party_id", "occurred_at"],
    )

    # ------------------------------------------------- append-only, in the database
    # Spelled out rather than generated in a loop: these four are the guards that make
    # the ledger immutable, and anyone auditing this file should be able to find them
    # by name.
    op.execute(_APPEND_ONLY_GUARD)
    op.execute(
        "CREATE TRIGGER finance_journal_entries_no_update "
        "BEFORE UPDATE ON finance_journal_entries "
        "FOR EACH ROW EXECUTE FUNCTION finance_refuse_ledger_mutation();"
    )
    op.execute(
        "CREATE TRIGGER finance_journal_entries_no_delete "
        "BEFORE DELETE ON finance_journal_entries "
        "FOR EACH ROW EXECUTE FUNCTION finance_refuse_ledger_mutation();"
    )
    op.execute(
        "CREATE TRIGGER finance_journal_postings_no_update "
        "BEFORE UPDATE ON finance_journal_postings "
        "FOR EACH ROW EXECUTE FUNCTION finance_refuse_ledger_mutation();"
    )
    op.execute(
        "CREATE TRIGGER finance_journal_postings_no_delete "
        "BEFORE DELETE ON finance_journal_postings "
        "FOR EACH ROW EXECUTE FUNCTION finance_refuse_ledger_mutation();"
    )

    # ------------------------------------------------- every entry balances, at COMMIT
    op.execute(_BALANCE_GUARD)
    op.execute(
        "CREATE CONSTRAINT TRIGGER finance_journal_postings_balance "
        "AFTER INSERT ON finance_journal_postings "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION finance_assert_entry_balances();"
    )

    # ------------------------------------------------------------- the aggregates
    op.create_table(
        "finance_driver_cash_accounts",
        sa.Column("account_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("limit_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("limit_currency", sa.String(length=3), nullable=False),
        sa.Column(
            "cod_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("cod_blocked_reason", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint(
            "driver_principal_id", name="uq_finance_driver_cash_account_principal"
        ),
        sa.CheckConstraint(
            "limit_minor_units > 0", name="ck_finance_cash_limit_is_positive"
        ),
        sa.CheckConstraint(
            "cod_blocked = false OR cod_blocked_reason IS NOT NULL",
            name="ck_finance_cod_block_has_a_reason",
        ),
    )

    op.create_table(
        "finance_merchant_accounts",
        sa.Column("account_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "payouts_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("payouts_blocked_reason", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("merchant_id", name="uq_finance_merchant_account"),
        sa.CheckConstraint(
            "payouts_blocked = false OR payouts_blocked_reason IS NOT NULL",
            name="ck_finance_payout_block_has_a_reason",
        ),
    )

    op.create_table(
        "finance_cod_collections",
        sa.Column("collection_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tracking_code", sa.String(length=32), nullable=False),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("goods_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("delivery_fee_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("driver_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settling_deposit_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("journal_entry_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("tracking_code", name="uq_finance_collection_tracking_code"),
        sa.CheckConstraint(
            "goods_minor_units >= 0 AND delivery_fee_minor_units >= 0",
            name="ck_finance_collection_amounts_not_negative",
        ),
        sa.CheckConstraint(
            "goods_minor_units + delivery_fee_minor_units > 0",
            name="ck_finance_collection_collects_something",
        ),
        # PAY-01, PAY-04 — cash becomes paid only through a hub-cashier deposit.
        sa.CheckConstraint(
            "channel <> 'CASH' OR settled_at IS NULL OR settling_deposit_id IS NOT NULL",
            name="ck_finance_settled_cash_names_its_deposit",
        ),
        sa.CheckConstraint(
            "channel <> 'CASH' OR driver_principal_id IS NOT NULL",
            name="ck_finance_cash_collection_names_the_driver",
        ),
        sa.CheckConstraint(
            "(settling_deposit_id IS NULL) OR (settled_at IS NOT NULL)",
            name="ck_finance_settling_deposit_implies_settled",
        ),
    )
    op.create_index(
        "ix_finance_collection_merchant",
        "finance_cod_collections",
        ["merchant_id", "collected_at"],
    )
    op.create_index(
        "ix_finance_collection_unsettled_driver",
        "finance_cod_collections",
        ["driver_principal_id", "collected_at"],
        postgresql_where=sa.text("settled_at IS NULL"),
    )

    op.create_table(
        "finance_deposits",
        sa.Column("deposit_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=24), nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("receipt_bucket", sa.String(length=128), nullable=False),
        sa.Column("receipt_key", sa.String(length=512), nullable=False),
        sa.Column("receipt_content_type", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hub_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=512), nullable=True),
        sa.Column("submitted_entry_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("settled_entry_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("reference", name="uq_finance_deposit_reference"),
        sa.CheckConstraint(
            "amount_minor_units > 0", name="ck_finance_deposit_amount_is_positive"
        ),
        # DRV-A07 — amount, reference and receipt, all three, for every method.
        sa.CheckConstraint(
            "receipt_bucket IS NOT NULL AND receipt_key IS NOT NULL",
            name="ck_finance_deposit_has_a_receipt",
        ),
        sa.CheckConstraint(
            "method <> 'HUB_CASHIER' OR hub_id IS NOT NULL",
            name="ck_finance_hub_deposit_names_the_hub",
        ),
        sa.CheckConstraint(
            "status = 'PENDING_VERIFICATION' OR ("
            "decided_at IS NOT NULL AND decided_by_actor_id IS NOT NULL)",
            name="ck_finance_deposit_decision_is_attributed",
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_finance_deposit_rejection_has_a_reason",
        ),
    )
    op.create_index(
        "ix_finance_deposit_driver",
        "finance_deposits",
        ["driver_principal_id", "submitted_at"],
    )
    op.create_index(
        "ix_finance_deposit_pending",
        "finance_deposits",
        ["submitted_at"],
        postgresql_where=sa.text("status = 'PENDING_VERIFICATION'"),
    )

    op.create_table(
        "finance_payout_requests",
        sa.Column("payout_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=24), nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("destination_reference", sa.String(length=128), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=512), nullable=True),
        # PAY-07 — always null today. Here so the day the procedure is defined is a code
        # change and not a migration on a live financial table.
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_entry_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint(
            "amount_minor_units > 0", name="ck_finance_payout_amount_is_positive"
        ),
        sa.CheckConstraint(
            "method = 'IN_PERSON_AT_HUB' OR destination_reference IS NOT NULL",
            name="ck_finance_payout_names_its_destination",
        ),
        sa.CheckConstraint(
            "status = 'REQUESTED' OR ("
            "decided_at IS NOT NULL AND decided_by_actor_id IS NOT NULL)",
            name="ck_finance_payout_decision_is_attributed",
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_finance_payout_rejection_has_a_reason",
        ),
        sa.CheckConstraint(
            "(status = 'PAID') = (paid_at IS NOT NULL)",
            name="ck_finance_payout_paid_at_matches_status",
        ),
    )
    op.create_index(
        "ix_finance_payout_merchant",
        "finance_payout_requests",
        ["merchant_id", "requested_at"],
    )
    op.create_index(
        "ix_finance_payout_open",
        "finance_payout_requests",
        ["requested_at"],
        postgresql_where=sa.text("status IN ('REQUESTED', 'APPROVED')"),
    )

    op.create_table(
        "finance_route_reconciliations",
        sa.Column("reconciliation_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("route_day", sa.Date(), nullable=False),
        sa.Column("expected_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("counted_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "parcels_returned_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "cash_settled_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("adjustment_entry_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint(
            "driver_principal_id",
            "route_day",
            name="uq_finance_reconciliation_driver_day",
        ),
        sa.CheckConstraint(
            "expected_minor_units >= 0 AND counted_minor_units >= 0",
            name="ck_finance_reconciliation_amounts_not_negative",
        ),
        sa.CheckConstraint(
            "(outcome = 'BALANCED' AND counted_minor_units = expected_minor_units) OR "
            "(outcome = 'SHORT' AND counted_minor_units < expected_minor_units) OR "
            "(outcome = 'OVER' AND counted_minor_units > expected_minor_units)",
            name="ck_finance_reconciliation_outcome_matches_the_count",
        ),
        sa.CheckConstraint(
            "status <> 'RESOLVED' OR resolved_at IS NOT NULL",
            name="ck_finance_reconciliation_resolved_has_a_time",
        ),
        sa.CheckConstraint(
            "outcome = 'BALANCED' OR status <> 'RESOLVED' OR ("
            "resolved_by_actor_id IS NOT NULL AND resolution_note IS NOT NULL)",
            name="ck_finance_mismatch_resolution_is_attributed",
        ),
    )
    op.create_index(
        "ix_finance_reconciliation_unresolved",
        "finance_route_reconciliations",
        ["opened_at"],
        postgresql_where=sa.text("status <> 'RESOLVED'"),
    )

    # ------------------------------------------------------------- messaging
    op.create_table(
        "finance_integration_outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column(
            "payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_owner", sa.String(length=128), nullable=True),
        sa.Column("processing_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_finance_outbox_event_id"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_finance_outbox_aggregate_version",
        ),
    )
    op.create_index(
        "ix_finance_outbox_pending",
        "finance_integration_outbox",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "finance_integration_inbox",
        sa.Column("inbox_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "consumer_name", "event_id", name="uq_finance_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_finance_inbox_status",
        "finance_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_finance_inbox_status", table_name="finance_integration_inbox")
    op.drop_table("finance_integration_inbox")
    op.drop_index("ix_finance_outbox_pending", table_name="finance_integration_outbox")
    op.drop_table("finance_integration_outbox")
    op.drop_index(
        "ix_finance_reconciliation_unresolved",
        table_name="finance_route_reconciliations",
    )
    op.drop_table("finance_route_reconciliations")
    op.drop_index("ix_finance_payout_open", table_name="finance_payout_requests")
    op.drop_index("ix_finance_payout_merchant", table_name="finance_payout_requests")
    op.drop_table("finance_payout_requests")
    op.drop_index("ix_finance_deposit_pending", table_name="finance_deposits")
    op.drop_index("ix_finance_deposit_driver", table_name="finance_deposits")
    op.drop_table("finance_deposits")
    op.drop_index(
        "ix_finance_collection_unsettled_driver", table_name="finance_cod_collections"
    )
    op.drop_index(
        "ix_finance_collection_merchant", table_name="finance_cod_collections"
    )
    op.drop_table("finance_cod_collections")
    op.drop_table("finance_merchant_accounts")
    op.drop_table("finance_driver_cash_accounts")

    # The guards go before the tables they protect, or the drops are refused.
    op.execute(
        "DROP TRIGGER IF EXISTS finance_journal_postings_balance "
        "ON finance_journal_postings;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS finance_journal_postings_no_update "
        "ON finance_journal_postings;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS finance_journal_postings_no_delete "
        "ON finance_journal_postings;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS finance_journal_entries_no_update "
        "ON finance_journal_entries;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS finance_journal_entries_no_delete "
        "ON finance_journal_entries;"
    )
    op.execute("DROP FUNCTION IF EXISTS finance_assert_entry_balances();")
    op.execute("DROP FUNCTION IF EXISTS finance_refuse_ledger_mutation();")

    op.drop_index("ix_finance_posting_account", table_name="finance_journal_postings")
    op.drop_index("ix_finance_posting_entry", table_name="finance_journal_postings")
    op.drop_table("finance_journal_postings")
    op.drop_index("ix_finance_entry_corrects", table_name="finance_journal_entries")
    op.drop_index("ix_finance_entry_subject", table_name="finance_journal_entries")
    op.drop_index("ix_finance_entry_occurred_at", table_name="finance_journal_entries")
    op.drop_index(
        "uq_finance_entry_idempotency_key", table_name="finance_journal_entries"
    )
    op.drop_table("finance_journal_entries")
