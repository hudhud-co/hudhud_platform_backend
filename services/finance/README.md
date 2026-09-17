# HUDHUD Finance Service

Bounded contexts owned: `wallet_cod` and `finance_settlement` (ADR-0012, which supersedes
ADR-0005).

COD receivable, driver cash custody and limits, deposits and exchange-office settlement,
the merchant payable, payout requests, refunds and the end-of-route reconciliation — all
of it on a double-entry ledger.

This service owns its own database, migrations, contracts and tests. It resolves callers
by asking Identity over HTTP; it never imports another service's package or reads its
tables, and no other service may write here.

## Requirements covered

| ID | Requirement |
|----|-------------|
| PAY-01 | A COD parcel counts as paid only when an online payment succeeded, a POS card was approved, or **cash reached the hub cashier** |
| PAY-02 | Including the receiver collecting and paying at a hub in person |
| PAY-03 | **No digital wallet payment option for receivers** |
| PAY-04 | An exchange-office transfer settles collected cash and confirms **no** original payment |
| PAY-05 | The merchant's running balance reflects confirmed deliveries and reconciled cash |
| PAY-06 | Payout on request: bank transfer · Mastercard · money-exchange partner · in person at a hub |
| PAY-07 | **Blocked** — the procedure per payout method needs an accountant |
| PAY-08 | **Partly blocked** — the charge is implemented; only the *waiver* is undecided |
| PAY-09 | HUDHUD refunds only a receiver who paid HUDHUD directly in advance |
| PAY-10 | The delivery fee is payable by the sender at pickup in cash |
| PAY-11 | All money is IQD |
| DRV-A05 | A driver's cash-on-hand record of collected COD |
| DRV-A06 | A per-driver cash limit, with utilisation |
| DRV-A07 | Deposit by exchange centre · bank transfer · hub cashier; amount, reference and receipt all required |
| DRV-A08 | An exchange transfer lets the driver keep collecting while an accountant verifies |
| DRV-A09 | A pending settlement frees the driver's limit before confirmation |
| DRV-A10 | Settlement history, with the receipt for each |
| DRV-A11 | The courier fee collected from the sender at pickup |
| DRV-A12 | End-of-day hub return: parcels handed over **and** cash settled, both confirmed |
| DRV-A13 | End-of-route reconciliation; a mismatch is investigated **before payout** |
| OPS-04 | Cash exposure: which drivers hold how much, and who is over limit |

## The ledger

Every movement of money is a **balanced journal entry**. There is no single-sided write
anywhere in this service, balances are summed from postings rather than stored, and a
mistake is corrected by posting the reverse — never by an edit.

Three guards enforce that in PostgreSQL rather than in Python, because each is a rule one
direct statement could otherwise break:

| Guard | How |
|---|---|
| The ledger is append-only | `BEFORE UPDATE` and `BEFORE DELETE` triggers on both ledger tables raise `restrict_violation` |
| Every entry balances | A **deferred** constraint trigger sums the postings at `COMMIT` and rejects the transaction if debits ≠ credits, if either side is missing, or if the currencies disagree |
| An account's party matches its kind | A check constraint: a party-scoped account names its party, a platform account does not |

`tests/new_service_migration_proof/test_finance_migration.py` proves all three by trying
to break them against a real PostgreSQL 16.

### Accounts

`DRIVER_CASH_CUSTODY` · `HUB_CASH` · `EXCHANGE_IN_TRANSIT` · `BANK` · `MERCHANT_PAYABLE`
· `HUDHUD_REVENUE` · `RECEIVER_REFUND_PAYABLE` — the seven ADR-0012 names, and no others.

`EXCHANGE_IN_TRANSIT` is the interesting one. v6.3 p.31 says an exchange-office transfer
lets the driver keep collecting **before** an accountant has verified the receipt, and
p.33 says that transfer is **not** a way to confirm the original payment. Both are true
at once only because the money sits somewhere that is neither the driver's custody nor
the bank.

## Money

Integer minor units with an explicit currency, end to end: `BigInteger` columns, integer
fields in every event payload and every API model. There is **no float, no `Decimal` and
no `NUMERIC` anywhere in this service** — asserted by a boundary test that sweeps the
source, and by a schema test that sweeps every column.

IQD has no minor unit in circulation, so one minor unit is one dinar. The scale is kept
explicit anyway, so a second currency cannot arrive by accident.

## Separation of duties

ADR-0012, enforced in the domain *and* at the adapter, because the person who hands money
over is never the person who says it arrived.

| Action | Who |
|---|---|
| Record a deposit | The driver, and only their own |
| Confirm a hub deposit | A hub cashier or an accountant |
| Verify an exchange receipt | An **accountant** — v6.3 p.31 names one specifically |
| Approve or reject a payout | **Operations only** |
| Resolve a cash mismatch | Operations |
| See platform cash exposure | Operations or an accountant — never a driver |

## The two Open Items

Both are v6.3 Appendix A items needing an accountant. Each blocks **one operation** and
nothing else. `GET /finance/open-items` reports both, so an operator meeting a 501 does
not have to guess whether it is a bug.

### PAY-07 — the payout procedure

`POST /finance/payouts/{id}/pay` answers **501**. What fee applies, when the money lands
and how the transfer is reconciled differ per method, and nobody has written that down.

Everything else works: the request, its method, its destination, the balance check,
approval, rejection, and the two facts those publish. The refusal posts nothing,
publishes nothing and marks nothing paid — proven against real PostgreSQL. `paid_at` and
`paid_entry_id` already exist on the table so the eventual decision is a code change, not
a migration on live financial data.

### PAY-08 — the return-fee waiver

The **charge is not blocked**. v6.3 p.38 and p.39 say a refusal charges the merchant both
the return-trip fee and the original delivery fee regardless of reason, and that is
implemented and posted. It takes no `reason` argument, because no reason changes it.

Only `POST /finance/refusal-charges/waive` answers **501**, because granting a waiver
would decide the question. The waiver is written in full behind
`FINANCE_RETURN_FEE_WAIVER_PERMITTED`, which is off and has no safe default on. A test
proves it works the moment the business permits it; another proves charging is never
coupled to the waiver being undecided.

## What is configuration, not code

* `FINANCE_DEFAULT_CASH_LIMIT` (DRV-A06). v6.3 says a per-driver limit exists; it never
  says what it is. **Production refuses to start without it** — an unset limit is not a
  cautious default, it is no limit at all.
* `FINANCE_RETURN_TRIP_FEE` (PAY-08's tariff). Without it a refusal charge is **refused**
  rather than guessed, and that answers 503, not 501: a number nobody set is a deployment
  problem, and setting it fixes it.

## Idempotency

A retried command must not move money twice, so there are three independent guards:

1. the **inbox** deduplicates a redelivered JetStream message per `(consumer, event_id)`;
2. a unique partial index on `finance_journal_entries.idempotency_key` makes a retried
   command find the entry it already posted;
3. a unique constraint on `finance_cod_collections.tracking_code` means one parcel has
   one collection whatever route reaches it.

`tests/test_inbox.py` drives real redeliveries through the real COD handler and checks the
ledger afterwards, rather than checking that a flag was set.

## Depends on

**Identity**, over HTTP token introspection, for every request. No Identity configured
means every request is denied — otherwise anyone could approve their own payout.

## Publishes

| Fact | When |
|------|------|
| `finance.fact.cod_settled` | A parcel's COD is actually paid (PAY-01) |
| `finance.fact.payout_requested` | A merchant asked to be paid |
| `finance.fact.payout_decided` | Operations approved or rejected it |
| `finance.fact.cash_limit_breached` | A driver is at or over their cash limit |

Each is written to the transactional outbox in the same transaction as the change that
caused it, and validated against its registered schema first. No payout destination and
no rejection text ever crosses the bus — only whether one exists.

## Consumes

`delivery.fact.cod_collected` and `delivery.fact.attempt_failed`. Delivery states what
happened at the door; Finance decides what it means for the books. **Delivery never
writes the ledger** — a boundary test asserts no other service imports this package or
owns a table that looks like a ledger.

## Validation

```bash
cd services/finance && uv run pytest
uv run ruff check .

# From the repository root: the migration and the real unit of work against PostgreSQL 16
uv run pytest tests/new_service_migration_proof/test_finance_migration.py
uv run pytest tests/new_service_migration_proof/test_finance_store.py
```

Migration head: `w27_finance_core_001`.
