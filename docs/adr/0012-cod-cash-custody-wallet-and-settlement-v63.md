# ADR-0012: COD, Driver Cash Custody, Wallet and Settlement (v6.3)

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** platform architecture review; product owner (v6.3 authorization)
- **Supersedes:** ADR-0005 (*Proposed — Policy Blocked*)
- **Implementation allowed:** yes

## Context

**[evidence]** ADR-0005 was blocked because the finance policy register P-01…P-17 had no decider.
*The Shipment Journey* v6.3 now supplies the missing rules as **Confirmed decisions**, and the
Driver App v8 money module and Customer App v3 wallet/payout screens supply the operational shape.
What remains undecided is narrow and is preserved as configuration, not invented.

**[evidence]** Binding rules taken verbatim from v6.3:

| Rule | Source |
|------|--------|
| A COD parcel counts as paid only when an online payment succeeded, a POS card payment was approved, or physical cash was collected **and handed to the hub cashier** | p.33 |
| Includes the receiver collecting and paying at a hub in person | p.33 |
| **No digital wallet payment option for receivers** | p.33 |
| Cash-transfer-via-exchange-office is **not** a way to confirm the original payment — only to settle cash already collected | p.33 |
| A COD parcel must not be marked Delivered unless payment was actually collected | p.31 |
| A failed card attempt is never a dead end — the driver falls back to cash | p.31 |
| Driver cash record and **per-driver cash limits** exist | p.31 |
| Exchange-office transfer lets the driver keep collecting while an accountant verifies the receipt | p.31, p.33 |
| End-of-route reconciliation checks cash handed over against what was expected; a mismatch is investigated before payout | p.31, p.33 |
| A refusal charges the merchant **both** the return-trip fee and the original delivery fee, regardless of reason | p.38, p.39 |
| Hudhud refunds only when the receiver paid Hudhud directly in advance | p.39 |
| A regular customer's parcel has **no COD** | p.8 |
| Payout methods: bank transfer, Mastercard, money-exchange partner, in-person hub collection | p.31, p.34 |

**[evidence]** Currency is IQD throughout (`fmtIQD` in the Driver App; `DEFAULT_COD_CURRENCY` in
the legacy monolith).

## Decision

**[decision]** `finance` is a service owning `wallet_cod` and `finance_settlement`. It is the only
context that recognises a merchant payable. Delivery publishes `delivery.fact.cod_collected`;
Finance consumes it. Delivery never credits a wallet.

**[decision]** Money is represented as an integer minor-unit amount plus an ISO currency code.
Floating-point arithmetic is forbidden for money anywhere in the platform. IQD has **zero** minor
units, so the stored integer is whole dinars; the representation still carries an explicit
`currency` so a second currency cannot be introduced by accident.

**[decision]** Finance keeps a double-entry ledger. Every posting is balanced; no single-sided
write exists. The account kinds required by v6.3 are: `DRIVER_CASH_CUSTODY`,
`HUB_CASH`, `EXCHANGE_IN_TRANSIT`, `BANK`, `MERCHANT_PAYABLE`, `HUDHUD_REVENUE`,
`RECEIVER_REFUND_PAYABLE`.

**[decision]** Cash custody lifecycle: collection debits `DRIVER_CASH_CUSTODY` and credits
`MERCHANT_PAYABLE` (goods) plus `HUDHUD_REVENUE` (fees). A deposit to a hub cashier moves
`DRIVER_CASH_CUSTODY` → `HUB_CASH` on confirmation. An exchange-office transfer moves
`DRIVER_CASH_CUSTODY` → `EXCHANGE_IN_TRANSIT` immediately — which is what frees the driver's limit
so they may keep collecting — and `EXCHANGE_IN_TRANSIT` → `BANK` only when an accountant verifies
the receipt. An unverified transfer therefore never counts as settled.

**[decision]** Card-on-POS collection never enters driver cash custody; it posts directly against
`BANK`.

**[decision]** A per-driver cash limit is enforced **as configuration**, not as an invented number:
the limit is a stored per-driver value with a platform default supplied by configuration. Exceeding
it blocks further COD assignment, not delivery — physical delivery stays irreversible.

**[decision]** Merchant payout is a request/approval workflow with the four v6.3 methods. The exact
operating procedure per method stays **[unresolved policy]** (v6.3 Appendix A, needs an accountant);
the platform models the request, its method, its destination and its state, and refuses to invent
fee or timing rules.

## Consequences

### Positive

- COD stops being a hole in the platform: custody, receipt, payable and settlement are auditable.
- The driver cash limit and exchange-office flow — both explicit v6.3 rules — become enforceable.

### Negative

- Ledger invariants must hold under concurrency; every posting needs a transaction and a balance
  assertion.

### Neutral

- Commission, tax and chart-of-accounts detail remain out of scope until an accountant defines them.

## Security

Money-moving commands are idempotent on a client key and authorized per role: a driver may record
their own deposit, only a hub cashier or accountant may confirm one, and only Operations may
approve a payout. Receipt images are referenced, never inlined.

## Rollback

Ledger entries are append-only. A mistaken posting is corrected by a compensating posting, never
by deletion or mutation.

## Unresolved questions

- Exact procedure per payout method (v6.3 Appendix A) — modelled as configuration.
- Commission and tax treatment — out of scope.
- High-value declared-value threshold — configuration, no default invented.
- Whether the return-trip fee may be waived — the rule as written always charges the merchant.

## References

- Supersedes ADR-0005. Depends on ADR-0003 (irreversible delivery), ADR-0011 (topology).
