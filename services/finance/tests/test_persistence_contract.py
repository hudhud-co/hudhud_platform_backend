"""Static guards on the Finance schema and its migration.

The ledger pair is the interesting part. A ``JournalEntry`` holds its postings as a
tuple, so the entity/row mapping is one-to-many rather than one-to-one, and that split
is asserted here rather than assumed.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from finance.domain.entities import (
    CodCollection,
    Deposit,
    DriverCashAccount,
    MerchantAccount,
    PayoutRequest,
    RouteReconciliation,
)
from finance.domain.ledger import JournalEntry, Posting
from finance.domain.messaging import InboxRecord, OutboxRecord
from finance.infrastructure.persistence.models import (
    Base,
    CodCollectionRow,
    DepositRow,
    DriverCashAccountRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    JournalEntryRow,
    JournalPostingRow,
    MerchantAccountRow,
    PayoutRequestRow,
    RouteReconciliationRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Composite value objects are flattened into columns and asserted separately below.
_PAIRS = (
    (DriverCashAccount, DriverCashAccountRow, {"limit"}),
    (MerchantAccount, MerchantAccountRow, set()),
    (CodCollection, CodCollectionRow, {"goods_amount", "delivery_fee"}),
    (Deposit, DepositRow, {"amount", "receipt"}),
    (PayoutRequest, PayoutRequestRow, {"amount"}),
    (RouteReconciliation, RouteReconciliationRow, {"expected", "counted"}),
    (OutboxRecord, IntegrationOutboxRow, set()),
    (InboxRecord, IntegrationInboxRow, set()),
)


def _migration_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_an_entry_and_its_postings_are_two_tables() -> None:
    """A tuple of postings cannot be a column, and flattening them would lose the pair."""
    entry_fields = {f.name for f in dataclasses.fields(JournalEntry)} - {"postings"}
    assert entry_fields <= set(JournalEntryRow.__table__.columns.keys())
    posting_fields = {f.name for f in dataclasses.fields(Posting)}
    posting_columns = set(JournalPostingRow.__table__.columns.keys())
    assert "side" in posting_columns
    assert {"amount", "account"} <= posting_fields
    # Both halves of each value object are present, flattened.
    assert {"amount_minor_units", "currency"} <= posting_columns
    assert {"account_kind", "party_id"} <= posting_columns


def test_money_is_flattened_into_minor_units_and_a_currency() -> None:
    for row_cls, prefix in (
        (DriverCashAccountRow, "limit"),
        (DepositRow, "amount"),
        (PayoutRequestRow, "amount"),
        (JournalPostingRow, "amount"),
        (JournalEntryRow, "total"),
    ):
        columns = set(row_cls.__table__.columns.keys())
        assert f"{prefix}_minor_units" in columns, row_cls.__name__


def test_every_monetary_column_is_a_big_integer() -> None:
    """An int32 caps near 2.1 billion. Silently wrapping money is never acceptable."""
    offenders = [
        f"{table.name}.{column.name}: {column.type}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name.endswith("_minor_units")
        and "BIGINT" not in str(column.type).upper()
    ]
    assert offenders == []


def test_the_ledger_tables_carry_no_version_column() -> None:
    """A version exists so a row can safely change. A posted entry never does."""
    for row_cls in (JournalEntryRow, JournalPostingRow):
        assert "version" not in row_cls.__table__.columns, row_cls.__name__


def test_every_mutable_aggregate_carries_a_version() -> None:
    for row_cls in (
        DriverCashAccountRow,
        MerchantAccountRow,
        CodCollectionRow,
        DepositRow,
        PayoutRequestRow,
        RouteReconciliationRow,
    ):
        assert "version" in row_cls.__table__.columns, row_cls.__name__


def test_a_receipt_is_a_pointer_and_not_the_bytes() -> None:
    columns = set(DepositRow.__table__.columns.keys())
    assert {"receipt_bucket", "receipt_key", "receipt_content_type"} <= columns


# ----------------------------------------------------- the migration matches


def test_every_model_column_appears_in_the_migration() -> None:
    source = _migration_source()
    missing = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if f'"{column.name}"' not in source
    ]
    assert missing == []


def test_the_migration_is_expand_only() -> None:
    source = _migration_source()
    assert "op.drop_column" not in source
    assert "op.alter_column" not in source


def test_the_migration_declares_a_head_and_no_parent() -> None:
    source = _migration_source()
    assert 'revision: str = "w27_finance_core_001"' in source
    assert "down_revision: str | Sequence[str] | None = None" in source


def test_the_revision_identifier_fits_alembics_column() -> None:
    for match in re.findall(r'revision: str = "([^"]+)"', _migration_source()):
        assert len(match) <= 32, match


def test_the_append_only_guard_is_in_the_migration() -> None:
    """The guard has to exist in the DDL, not only in this service's good manners."""
    source = _migration_source()
    assert "finance_refuse_ledger_mutation" in source
    for table in ("finance_journal_entries", "finance_journal_postings"):
        assert f"{table}_no_update" in source
        assert f"{table}_no_delete" in source


def test_the_balance_guard_is_a_deferred_constraint_trigger() -> None:
    """A balance is a property of a set of rows, so it cannot be a CHECK.

    Deferring it to COMMIT is what lets an ordinary two-statement insert work at all.
    """
    source = _migration_source()
    assert "finance_assert_entry_balances" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "does not balance" in source


def test_the_guards_are_dropped_before_the_tables_they_protect() -> None:
    """Otherwise the downgrade fails on its own trigger."""
    source = _migration_source()
    downgrade = source[source.index("def downgrade"):]
    drop_trigger = downgrade.index("DROP TRIGGER IF EXISTS finance_journal_postings_no")
    drop_table = downgrade.index('op.drop_table("finance_journal_postings")')
    assert drop_trigger < drop_table


def test_the_invariants_reach_the_database() -> None:
    source = _migration_source()
    for constraint in (
        "ck_finance_posting_party_matches_account_kind",
        "ck_finance_posting_amount_is_positive",
        "ck_finance_settled_cash_names_its_deposit",
        "ck_finance_cash_collection_names_the_driver",
        "ck_finance_deposit_decision_is_attributed",
        "ck_finance_deposit_has_a_receipt",
        "ck_finance_payout_names_its_destination",
        "ck_finance_reconciliation_outcome_matches_the_count",
        "ck_finance_mismatch_resolution_is_attributed",
        "uq_finance_entry_idempotency_key",
        "uq_finance_outbox_aggregate_version",
        "uq_finance_inbox_consumer_event",
    ):
        assert constraint in source, constraint


def test_the_payout_paid_columns_exist_although_pay_07_blocks_them() -> None:
    """Here now so the day the procedure is defined is a code change, not a migration
    against a live financial table."""
    columns = set(PayoutRequestRow.__table__.columns.keys())
    assert {"paid_at", "paid_entry_id"} <= columns
    assert "ck_finance_payout_paid_at_matches_status" in _migration_source()
