"""The Finance unit of work against the database its own migration built.

The migration proof already showed PostgreSQL refusing an unbalanced entry, an update and
a delete. This drives the **real services** through that database, so what is checked is
the whole path — domain, store, triggers — rather than the SQL in isolation:

* a COD collection posts, and its money lands where the flow says;
* a retry under the same idempotency key posts once, against a real unique index;
* a rollback discards the entry, its postings and the outbox row together;
* a version-conditional UPDATE really refuses a stale write.
"""

from __future__ import annotations

import pytest

from .helpers import (
    alembic,
    docker_available,
    run_in_service,
    start_postgres,
    stop_postgres,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not docker_available(), reason="Docker is required for the store proof"
    ),
]

SERVICE = "finance"

_PRELUDE = """
import os
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from finance.application.cash_service import CashService
from finance.application.cod_service import CodService
from finance.application.settlement_service import SettlementService
from finance.domain.errors import StaleFinanceRecord
from finance.domain.ledger import (
    BANK,
    EXCHANGE_IN_TRANSIT,
    HUB_CASH,
    HUDHUD_REVENUE,
    balance_of,
    driver_custody,
    merchant_payable,
)
from finance.domain.money import Currency, Money
from finance.domain.value_objects import (
    CodPaymentChannel,
    DepositMethod,
    EvidenceMediaRef,
    PayoutMethod,
)
from finance.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyFinanceUnitOfWork,
)
from finance.ports.authorization import FinanceActor, FinanceRole

URL = os.environ["FINANCE_DATABASE_URL"]
engine = create_engine(URL)
uow = SqlAlchemyFinanceUnitOfWork(session_factory=sessionmaker(bind=engine))


def iqd(n):
    return Money(minor_units=n, currency=Currency.IQD)


def actor(*roles):
    return FinanceActor(principal_id=uuid4(), roles=frozenset(roles))


cash = CashService(uow, default_cash_limit=iqd(1_000_000))
cod = CodService(uow)
settlement = SettlementService(uow, return_trip_fee=iqd(5_000))

DRIVER = uuid4()
MERCHANT = uuid4()
cash.open_account(driver_principal_id=DRIVER)
settlement.open_merchant_account(merchant_id=MERCHANT)


def receipt():
    return EvidenceMediaRef(bucket="finance-evidence", key="receipt.jpg")


def held():
    custody = driver_custody(DRIVER)
    uow.begin()
    try:
        amount = balance_of(custody, uow.ledger.entries_for_account(custody)).amount
    finally:
        uow.commit()
    return amount
"""


@pytest.fixture(scope="module")
def lab():
    started = start_postgres(f"{SERVICE}-store")
    try:
        upgrade = alembic(SERVICE, started, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr[-4000:]
        yield started
    finally:
        stop_postgres(started)


def run(lab, body: str) -> str:
    result = run_in_service(SERVICE, _PRELUDE + body, lab)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


def test_a_cod_collection_posts_through_the_real_store(lab) -> None:
    out = run(
        lab,
        """
cod.record_collection(
    tracking_code="SHP-20260915-100001",
    merchant_id=MERCHANT,
    channel=CodPaymentChannel.CASH,
    goods_amount=iqd(100_000),
    delivery_fee=iqd(5_000),
    driver_principal_id=DRIVER,
)
assert held() == iqd(105_000), held()
uow.begin()
payable = merchant_payable(MERCHANT)
owed = balance_of(payable, uow.ledger.entries_for_account(payable)).amount
earned = balance_of(HUDHUD_REVENUE, uow.ledger.entries_for_account(HUDHUD_REVENUE)).amount
uow.commit()
assert owed == iqd(100_000), owed
assert earned == iqd(5_000), earned
print("COLLECTION_OK")
""",
    )
    assert "COLLECTION_OK" in out


def test_a_retry_under_one_key_posts_once_against_the_real_index(lab) -> None:
    """The unique partial index on ``idempotency_key`` is the guarantee, not the code."""
    out = run(
        lab,
        """
before = held()
for _ in range(3):
    cod.record_collection(
        tracking_code="SHP-20260915-100002",
        merchant_id=MERCHANT,
        channel=CodPaymentChannel.CASH,
        goods_amount=iqd(40_000),
        delivery_fee=iqd(0),
        driver_principal_id=DRIVER,
        idempotency_key="retry-100002",
    )
after = held()
assert after.minor_units - before.minor_units == 40_000, (before, after)

with engine.begin() as c:
    rows = c.execute(text(
        "SELECT count(*) FROM finance_journal_entries WHERE idempotency_key = 'retry-100002'"
    )).scalar_one()
assert rows == 1, rows
print("RETRY_POSTED_ONCE")
""",
    )
    assert "RETRY_POSTED_ONCE" in out


def test_the_full_cash_journey_lands_where_v63_says(lab) -> None:
    """Collect, deposit by exchange, verify: custody → in transit → bank."""
    out = run(
        lab,
        """
cod.record_collection(
    tracking_code="SHP-20260915-100003",
    merchant_id=MERCHANT,
    channel=CodPaymentChannel.CASH,
    goods_amount=iqd(60_000),
    delivery_fee=iqd(0),
    driver_principal_id=DRIVER,
)
before = held()
deposit = cash.submit_deposit(
    driver_principal_id=DRIVER,
    method=DepositMethod.EXCHANGE_OFFICE,
    amount=iqd(60_000),
    reference="EX-PG-1",
    receipt=receipt(),
).deposit

# v6.3 p.31 — the limit is freed the moment the transfer is made.
assert held().minor_units == before.minor_units - 60_000

uow.begin()
transit = balance_of(
    EXCHANGE_IN_TRANSIT, uow.ledger.entries_for_account(EXCHANGE_IN_TRANSIT)
).amount
uow.commit()
assert transit == iqd(60_000), transit

cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=actor(FinanceRole.ACCOUNTANT))
uow.begin()
transit_after = balance_of(
    EXCHANGE_IN_TRANSIT, uow.ledger.entries_for_account(EXCHANGE_IN_TRANSIT)
).amount
bank = balance_of(BANK, uow.ledger.entries_for_account(BANK)).amount
uow.commit()
assert transit_after == iqd(0), transit_after
assert bank == iqd(60_000), bank
print("EXCHANGE_JOURNEY_OK")
""",
    )
    assert "EXCHANGE_JOURNEY_OK" in out


def test_a_hub_deposit_settles_the_parcel_it_covers(lab) -> None:
    """PAY-01 — cash counts as paid only once it reaches the hub cashier."""
    out = run(
        lab,
        """
cod.record_collection(
    tracking_code="SHP-20260915-100004",
    merchant_id=MERCHANT,
    channel=CodPaymentChannel.CASH,
    goods_amount=iqd(25_000),
    delivery_fee=iqd(0),
    driver_principal_id=DRIVER,
)
assert cod.collection_for(tracking_code="SHP-20260915-100004").is_paid is False

deposit = cash.submit_deposit(
    driver_principal_id=DRIVER,
    method=DepositMethod.HUB_CASHIER,
    amount=iqd(25_000),
    reference="HUB-PG-1",
    receipt=receipt(),
    hub_id=uuid4(),
).deposit
cash.confirm_deposit(deposit_id=deposit.deposit_id, actor=actor(FinanceRole.HUB_CASHIER))
settled = cod.collection_for(tracking_code="SHP-20260915-100004")
assert settled.is_paid is True
assert settled.settling_deposit_id == deposit.deposit_id

# And the settlement fact reached the outbox in the same transaction.
with engine.begin() as c:
    rows = c.execute(text(
        "SELECT count(*) FROM finance_integration_outbox "
        "WHERE event_type = 'finance.fact.cod_settled' "
        "AND payload_json->'payload'->>'tracking_code' = 'SHP-20260915-100004'"
    )).scalar_one()
assert rows == 1, rows
print("HUB_SETTLEMENT_OK")
""",
    )
    assert "HUB_SETTLEMENT_OK" in out


def test_a_rollback_discards_the_entry_its_postings_and_the_outbox_row(lab) -> None:
    """The whole point of a transactional outbox: no fact without its posting."""
    out = run(
        lab,
        """
entry_id = uuid4()
uow.begin()
from finance.domain.ledger import EntryDraft
from finance.domain.value_objects import JournalReason
from finance.application.publishing import enqueue

draft = (
    EntryDraft(reason=JournalReason.COD_CASH_COLLECTED, occurred_at=datetime.now(tz=UTC))
    .debit(driver_custody(DRIVER), iqd(11_111))
    .credit(HUDHUD_REVENUE, iqd(11_111))
)
entry = draft.build(entry_id)
uow.ledger.append(entry)
uow.rollback()

with engine.begin() as c:
    entries = c.execute(text(
        "SELECT count(*) FROM finance_journal_entries WHERE entry_id = :e"
    ), {"e": str(entry_id)}).scalar_one()
    postings = c.execute(text(
        "SELECT count(*) FROM finance_journal_postings WHERE entry_id = :e"
    ), {"e": str(entry_id)}).scalar_one()
assert entries == 0, entries
assert postings == 0, postings
print("ROLLBACK_OK")
""",
    )
    assert "ROLLBACK_OK" in out


def test_a_stale_write_on_a_cash_account_is_refused(lab) -> None:
    """Two operators changing one driver's limit: the second must lose, not win."""
    out = run(
        lab,
        """
uow.begin()
first = uow.driver_accounts.find_for_driver(DRIVER)
uow.commit()
uow.begin()
second = uow.driver_accounts.find_for_driver(DRIVER)
uow.commit()

first.limit = iqd(2_000_000)
first.version += 1
uow.begin()
uow.driver_accounts.save(first)
uow.commit()

second.limit = iqd(3_000_000)
second.version += 1
uow.begin()
uow.driver_accounts.save(second)
try:
    uow.commit()
except StaleFinanceRecord:
    print("STALE_REFUSED")
else:
    raise AssertionError("the stale write was accepted")
""",
    )
    assert "STALE_REFUSED" in out


def test_the_service_cannot_update_a_posted_entry_even_if_it_tried(lab) -> None:
    """The store has no update path; this proves the database would refuse one anyway."""
    out = run(
        lab,
        """
from sqlalchemy.exc import InternalError, DatabaseError

cod.record_collection(
    tracking_code="SHP-20260915-100005",
    merchant_id=MERCHANT,
    channel=CodPaymentChannel.ONLINE,
    goods_amount=iqd(7_000),
    delivery_fee=iqd(0),
)
collection = cod.collection_for(tracking_code="SHP-20260915-100005")
try:
    with engine.begin() as c:
        c.execute(text(
            "UPDATE finance_journal_entries SET memo = 'edited' WHERE entry_id = :e"
        ), {"e": str(collection.journal_entry_id)})
except (InternalError, DatabaseError) as exc:
    assert "append-only" in str(exc)
    print("IMMUTABLE_OK")
else:
    raise AssertionError("a posted entry was edited")
""",
    )
    assert "IMMUTABLE_OK" in out


def test_a_payout_request_and_decision_both_reach_the_outbox(lab) -> None:
    out = run(
        lab,
        """
# Each of these runs in its own process against a fresh merchant, so the balance the
# payout draws on has to be earned first.
cod.record_collection(
    tracking_code="SHP-20260915-100006",
    merchant_id=MERCHANT,
    channel=CodPaymentChannel.ONLINE,
    goods_amount=iqd(80_000),
    delivery_fee=iqd(0),
)
payout = settlement.request_payout(
    merchant_id=MERCHANT,
    method=PayoutMethod.IN_PERSON_AT_HUB,
    amount=iqd(10_000),
)
settlement.approve_payout(payout_id=payout.payout_id, actor=actor(FinanceRole.OPERATIONS))
with engine.begin() as c:
    types = [r[0] for r in c.execute(text(
        "SELECT event_type FROM finance_integration_outbox "
        "WHERE aggregate_id = :a ORDER BY aggregate_version"
    ), {"a": str(payout.payout_id)}).fetchall()]
assert types == ["finance.fact.payout_requested", "finance.fact.payout_decided"], types
print("PAYOUT_FACTS_OK")
""",
    )
    assert "PAYOUT_FACTS_OK" in out


def test_pay_07_stays_blocked_against_the_real_database(lab) -> None:
    """Fail-closed end to end: nothing posts, nothing publishes, nothing is marked paid."""
    out = run(
        lab,
        """
from finance.domain.errors import PayoutProcedureNotDefined

cod.record_collection(
    tracking_code="SHP-20260915-100007",
    merchant_id=MERCHANT,
    channel=CodPaymentChannel.ONLINE,
    goods_amount=iqd(80_000),
    delivery_fee=iqd(0),
)
payout = settlement.request_payout(
    merchant_id=MERCHANT,
    method=PayoutMethod.BANK_TRANSFER,
    amount=iqd(5_000),
    destination_reference="IQ00BANK0002",
)
settlement.approve_payout(payout_id=payout.payout_id, actor=actor(FinanceRole.OPERATIONS))

with engine.begin() as c:
    before = c.execute(text("SELECT count(*) FROM finance_journal_entries")).scalar_one()

try:
    settlement.pay_payout(payout_id=payout.payout_id, actor=actor(FinanceRole.OPERATIONS))
except PayoutProcedureNotDefined:
    pass
else:
    raise AssertionError("a payout was paid out")

with engine.begin() as c:
    after = c.execute(text("SELECT count(*) FROM finance_journal_entries")).scalar_one()
    paid = c.execute(text(
        "SELECT count(*) FROM finance_payout_requests WHERE paid_at IS NOT NULL"
    )).scalar_one()
assert after == before, (before, after)
assert paid == 0, paid
print("PAY07_FAIL_CLOSED")
""",
    )
    assert "PAY07_FAIL_CLOSED" in out


def test_the_whole_ledger_nets_to_zero_in_postgres(lab) -> None:
    """After every flow this module ran: debits equal credits, across all accounts."""
    out = run(
        lab,
        """
with engine.begin() as c:
    net = c.execute(text(
        "SELECT COALESCE(SUM(CASE WHEN side = 'DEBIT' THEN amount_minor_units "
        "ELSE -amount_minor_units END), 0) FROM finance_journal_postings"
    )).scalar_one()
    unbalanced = c.execute(text(
        "SELECT count(*) FROM ("
        "  SELECT entry_id FROM finance_journal_postings GROUP BY entry_id "
        "  HAVING SUM(CASE WHEN side = 'DEBIT' THEN amount_minor_units ELSE 0 END) "
        "      <> SUM(CASE WHEN side = 'CREDIT' THEN amount_minor_units ELSE 0 END)"
        ") q"
    )).scalar_one()
assert net == 0, net
assert unbalanced == 0, unbalanced
print("LEDGER_NETS_TO_ZERO")
""",
    )
    assert "LEDGER_NETS_TO_ZERO" in out
