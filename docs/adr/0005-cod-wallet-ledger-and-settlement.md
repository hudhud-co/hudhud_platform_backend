# ADR-0005: COD, Wallet, Double-Entry Ledger, Settlement, and Reconciliation

- **Status:** Proposed — Policy Blocked
- **Date:** 2026-08-30
- **Deciders:** (pending — finance, operations, and platform architecture review)
- **Workstream:** W1-E
- **Implementation allowed:** no

Label key: **[evidence]** verified from repository or legacy audit; **[proposal]** recommended design not yet accepted; **[decision]** binding only after acceptance; **[assumption]** engineering default pending validation; **[unresolved policy]** requires named deciders and must not be invented here.

---

## Context

**[evidence]** Platform invariants (`architecture/invariants.md` §Physical Delivery and Finance) require:

- Physical delivery is an irreversible operational fact.
- Finance failures must never roll back physical delivery.
- COD collection and merchant wallet/payable recognition are separate accounting facts.

**[evidence]** `architecture/service-boundaries.yaml` declares bounded contexts `wallet_cod` (legacy module `wallet`) and `finance_settlement` (`proposed_platform_owner: finance`, `extraction_status: not_started`, `policy_prerequisites: [double_entry_ledger_adr, settlement_policy_adr]`). `finance_settlement` is marked `transitional_deployable_candidate: policy_blocked`.

**[evidence]** Legacy monolith (`hudhud-backend` @ `2e375057fdf9b9ce8416408a4436303be5301def`) implements partial COD collection and merchant wallet credit but explicitly defers settlement, reconciliation, driver/hub cash custody, commission, and double-entry general ledger. Legacy dirty file `scripts/dev_pickup_driver_simulator.py` was not inspected or modified during this ADR preparation.

**[proposal]** This ADR separates physical COD collection, cash custody, accounting receipt, merchant payable recognition, wallet projection, settlement, and payout — and defines an engineering/accounting foundation without inventing legal, tax, fee, commission, payout, or operational policy.

---

## Verified legacy evidence

### COD configuration and personal vs merchant rules

| Topic | Legacy evidence | Notes |
|-------|-----------------|-------|
| Seller-only COD entitlement | `app/modules/send_parcel/domain/cod_eligibility.py` — `is_cod_allowed_for_channel`, `personal_channel_blocks_positive_cod` | **[evidence]** `CUSTOMER_DIRECT` (personal send) must not carry positive goods COD |
| COD on shipment | `shipment/infrastructure/models.py` — `payment_required`, `cod_amount`; copied from order at create | **[evidence]** Authoritative collectable amount on shipment |
| Customer-safe projection | `shipment/application/customer_cod_collection_view.py` — `project_customer_safe_cod_summary` | **[evidence]** Collection outcome derived from `delivery_cod_collections` + `DELIVERED`, not wallet |
| Default currency | `pickup/application/pickup_policy_evaluator.py` — `DEFAULT_COD_CURRENCY` (`IQD`) | **[evidence]** Used in completion policy and wallet credit |

### Delivery completion and COD collection

| Symbol / table | Module | Role |
|----------------|--------|------|
| `CompleteDeliveryTaskUseCase` | `delivery_task/application/complete_delivery_task.py` | **[evidence]** Authoritative driver delivery completion orchestrator |
| `resolve_cod_collection` | `delivery_task/application/completion_policy.py` | **[evidence]** Requires `cod_collected=true` when `payment_required` and `cod_amount > 0`; forbids COD input on non-COD shipments; `collected_amount` must equal `expected_amount` (no partial/over collection in schema) |
| `DeliveryCodCollectionORM` | `delivery_task/infrastructure/models.py` | **[evidence]** Table `delivery_cod_collections`; `uq_delivery_cod_collections_shipment_id`; check `collected_amount = expected_amount` |
| Migration | `alembic/versions/o1p2q3r4s5t6_add_delivery_completion_integrity.py` | **[evidence]** Creates `delivery_cod_collections` |
| Ops override | `shipment/application/delivery_completion.py` — `MarkShipmentDeliveredUseCase` | **[evidence]** Does **not** create COD collection or wallet credit (`docs/audits/phase15_1_merchant_cod_balance_integrity.md`) |

### Wallet accounts, ledger, and credit semantics

| Symbol / table | Module | Role |
|----------------|--------|------|
| `WalletAccountORM` | `wallet/infrastructure/models.py` | **[evidence]** `account_scope=STORE`; one ACTIVE wallet per `merchant_id` (partial unique index) |
| `WalletLedgerEntryORM` | `wallet/infrastructure/models.py` | **[evidence]** Append-only entries with `entry_type`, `amount > 0`, `balance_after`, `idempotency_key` |
| `LedgerEntryType` | `wallet/domain/enums.py` | **[evidence]** `CREDIT`, `DEBIT`, `ADJUSTMENT`, `PAYOUT_HOLD`, `PAYOUT_RELEASE` |
| `CreditCodCollectedToMerchantWalletUseCase` | `wallet/application/credit_cod_collection.py` | **[evidence]** Idempotency `cod_collected:{shipment_id}`; skips customer-direct; resolves `merchant_id` from shipment/order; docstring: "Does not settle, pay out, or reconcile" |
| `RecordWalletLedgerEntryUseCase` | `wallet/application/record_wallet_ledger_entry.py` | **[evidence]** Account lock, idempotency pre-check, `balance_after` computation, audit append |
| `ledger_balance_delta` | `wallet/domain/validation.py` | **[evidence]** CREDIT/ADJUSTMENT +amount; DEBIT −amount; PAYOUT_HOLD/RELEASE delta 0 on ledger balance |
| `build_wallet_summary` | `wallet/application/mapping.py` | **[evidence]** `ledger_balance = credits − debits`; `held_balance = holds − releases`; `available = ledger − held` |
| Migrations | `p3e4f5a6b7c8_add_wallet_tables.py`, `t0u1v2w3x4y5_store_scoped_wallet_accounts.py` | **[evidence]** Wallet schema + Phase 1B store consolidation |
| Phase docs | `docs/production_v1/store_wallet_phase_1b.md`, `docs/api/wallet_api.md` | **[evidence]** Deferred: settlement, reconciliation, driver/hub cash, payout APPROVED/PAID admin flows |

### Payout requests (partial)

| Symbol | Evidence |
|--------|----------|
| `PayoutRequestORM` | `wallet/infrastructure/models.py` — statuses include `REQUESTED`, `APPROVED`, `REJECTED`, `PAID`, `CANCELLED` |
| `CreatePayoutRequestUseCase` | Creates `REQUESTED` + `PAYOUT_HOLD` ledger entry (`payout_hold:{id}`) |
| `CancelPayoutRequestUseCase` | Cancels `REQUESTED` + `PAYOUT_RELEASE` (`payout_release:{id}`) |
| Missing | **[evidence]** No use case in `app/modules/wallet/` transitions payout to `APPROVED`, `REJECTED`, or `PAID` |

### Merchant authorization

| Topic | Evidence |
|-------|----------|
| Wallet API access | `wallet/application/access_policy.py` → `ensure_reseller_merchant_access(..., require_wallet_enabled=True)` |
| Store capability | `auth/application/store_capabilities.py` — `ACCESS_STORE_WALLET` (Owner-only in Phase 1B tests) |
| COD credit authorization | **[evidence]** Credit keys on `merchant_id` only; driver actor recorded in metadata/audit, not wallet ownership |

### Idempotency and retries

| Layer | Key / constraint | Evidence |
|-------|------------------|----------|
| Wallet ledger | `(wallet_account_id, idempotency_key)` unique | `uq_wallet_ledger_entries_account_idempotency` |
| COD credit | `cod_collected:{shipment_id}` | `credit_cod_collection.py` |
| COD collection | one row per shipment | `uq_delivery_cod_collections_shipment_id` |
| Delivery completion replay | completed task + delivered shipment → idempotent return | `complete_delivery_task.py` lines 133–149 |
| HTTP action idempotency | `delivery_action_idempotency_keys` table | `delivery_task/infrastructure/models.py` |
| IntegrityError handling | ledger append catches duplicate idempotency | `record_wallet_ledger_entry.py` |

### Tests (representative)

| Test file | Coverage |
|-----------|----------|
| `tests/integration/modules/delivery_task/test_cod_wallet_credit_api.py` | **[evidence]** End-to-end COD delivery → single CREDIT, idempotency replay |
| `tests/unit/modules/delivery_task/test_complete_delivery_cod_wallet.py` | **[evidence]** Unit integrity of completion + wallet coupling |
| `tests/unit/modules/wallet/test_wallet_use_cases.py` | Ledger, payout hold/release, summary |
| `tests/unit/modules/wallet/test_wallet_phase1b_boundary.py` | Store-scoped wallet, capability gates |
| `tests/integration/migration/test_store_wallet_phase1b_migration.py` | Phase 1B consolidation migration |

Platform audit mirrors: `docs/audit/legacy-data-ownership-inventory.md` §Wallet and COD State Writers; `docs/audit/legacy-domain-inventory.md` §Wallet and COD.

---

## Current coupling and failure analysis

### Transaction boundary (legacy)

**[evidence]** Phase 15.1 audit documents a single API session commit:

```
CompleteDeliveryTaskUseCase
  → create delivery_cod_collections
  → CreditCodCollectedToMerchantWalletUseCase
       → get_or_create wallet_accounts
       → RecordWalletLedgerEntryUseCase (CREDIT)
  → mark task COMPLETED / shipment DELIVERED
  → shipment_events / audit / notifications
API wrapper → session.commit() once
```

(source: `docs/audits/phase15_1_merchant_cod_balance_integrity.md` §Transaction boundary)

**[evidence]** Wallet failure before commit rolls back delivery, COD collection, and credit together. Merchant ownership unresolved (`MerchantWalletOwnershipUnresolvedAppException`) also rolls back completion (`phase15_1` §Wallet ownership resolution).

| Coupling | Legacy behavior | Platform invariant gap |
|----------|-----------------|------------------------|
| Delivery ↔ COD collection | Same use case, same DB transaction | Acceptable as operational fact co-recording if delivery is committed first on platform |
| Delivery ↔ wallet credit | Same transaction; credit failure aborts delivery | **Violates** "finance failures must not roll back physical delivery" |
| Delivery ↔ shipment status | `delivery_task` mutates `shipments.current_status` directly | Violates shipment sole-writer (ADR-0003 scope) |
| COD collection ↔ wallet credit | Sequential in-process call | Conflates physical collection with payable recognition |
| Shipment status ↔ wallet | Credit only on driver completion path, not ops `MarkShipmentDelivered` | Intentional partial separation |

### Failure modes

| Failure | Legacy outcome | Desired platform behavior (proposal) |
|---------|----------------|-------------------------------------|
| Wallet credit error during completion | Full rollback — not delivered, no COD row | Delivery + COD collection committed; finance posting retried or suspended |
| Merchant ownership unresolved | Full rollback | Delivery + COD committed; payable recognition in suspense pending resolution |
| Duplicate completion request | Idempotent success if already delivered | Same, with outbox/inbox dedupe across services |
| Finance posting lag | N/A (synchronous) | Merchant wallet projection eventually consistent; ops visibility into backlog |
| Payout hold insufficient balance | Payout request rejected | Unchanged at wallet layer; settlement layer separate |

### Legacy wallet model characterization

**[evidence]** The legacy wallet is a **mixed model**:

| Characteristic | Present? | Evidence |
|----------------|----------|----------|
| Append-only transaction history | Yes | Ledger entries never updated/deleted (`wallet_api.md` §Ledger rules) |
| Mutable account balance column | No | Balances derived from ledger aggregates |
| Per-entry running balance projection | Yes | `balance_after` on each entry |
| Single-entry operational ledger | Yes | One-sided CREDIT/DEBIT per merchant wallet account |
| Double-entry journal (balanced pairs) | No | No second leg, no chart of accounts |
| Authoritative general ledger | No | Wallet ledger is merchant payable **projection**, not company books |
| Physical cash custody tracking | No | Explicitly out of scope (`store_wallet_phase_1b.md`, `wallet_api.md`) |

**[proposal]** Treat legacy wallet as a **merchant-facing payable projection** with append-only single-entry history — not an authoritative company ledger.

---

## Options

### Accounting authority models

| Option | Summary | Trade-offs |
|--------|---------|------------|
| **1. Continue wallet-credit model** | Extend legacy `wallet_ledger_entries` as system of record for COD, commission, settlement | Minimal migration; conflates projection with authority; no driver/hub cash legs; weak audit for company books |
| **2. Append-only single-entry operational ledger** | Central operational ledger (single-sided entries) separate from wallet UI balance | Simpler than double-entry; still lacks automatic balance enforcement; reconciliation harder |
| **3. Immutable double-entry journal as authority; wallet as projection** | Finance service owns balanced journal; wallet balances are derived read models | Strongest accounting integrity; higher implementation cost; requires chart-of-accounts policy |

### Event integration (cross-cutting)

| Option | Summary | Trade-offs |
|--------|---------|------------|
| A. Synchronous in-process (legacy) | Delivery calls wallet in same transaction | Simple; violates service boundaries and rollback invariant |
| B. Same-aggregate outbox, async finance consumer | Delivery commits facts; finance consumes `delivery.fact.cod_collected` | Matches platform messaging; eventual wallet consistency |
| C. Choreography without inbox | Direct HTTP to finance after delivery | Tight coupling; retry burden on delivery |

---

## Decision drivers

Ranked constraints:

1. **[decision]** Platform invariant: physical delivery and COD collection must not roll back on finance failure (`architecture/invariants.md`).
2. **[decision]** Service independence: no cross-service DB access; Finance owns its database (`architecture/invariants.md` §Service Independence).
3. **[evidence]** Legacy gaps: no double-entry, no cash custody, no settlement pipeline — greenfield finance layer required.
4. **[proposal]** Auditability and reconciliation for operations and future accounting review.
5. **[unresolved policy]** Business rules for commission, settlement frequency, and payout channels block schema finalization.
6. **[proposal]** Migration from legacy monolith must preserve idempotency keys and not dual-write bidirectionally.

---

## Decision

**[proposal]** Adopt **Option 3**: an immutable **double-entry journal** in the `finance_settlement` bounded context as the accounting authority; **wallet** remains a **merchant payable projection** updated from finance posting outcomes. Physical COD collection remains an operational fact owned by **delivery**; cash custody transitions are operational facts owned by **delivery** and **hub** (policy pending); finance consumes those facts asynchronously.

**[proposal]** Do **not** implement Finance in this ADR. Status remains **Proposed — Policy Blocked** until policy decision register items have named deciders.

This is a **recommendation**, not an accepted decision.

---

## Proposed authority model

```mermaid
flowchart LR
  subgraph operational [Operational facts - irreversible]
    DT[Delivery: physical delivery + COD collection record]
    HB[Hub: cash handover evidence - policy TBD]
  end
  subgraph finance [Finance - accounting authority]
    J[Immutable double-entry journal]
    EX[Posting exceptions / suspense]
  end
  subgraph projections [Projections]
    W[Wallet merchant payable balance]
    R[Reconciliation work queue]
  end
  DT -->|delivery.fact.cod_collected (CodCollected)| J
  DT -->|shipment.fact.delivered (ShipmentDelivered) via Shipment| J
  HB -->|hub.fact.cash_received - TBD| J
  J --> W
  J --> R
  EX --> R
```

**[decision]** Finance consumes **`CodCollected`** and **`ShipmentDelivered`** separately, correlating
by `shipment_id`. Either may arrive first. Wallet is updated only from Finance-authorized postings —
never directly from Delivery.

| Fact layer | Canonical writer | Must survive finance failure? |
|------------|------------------|-------------------------------|
| Physical delivery completion | delivery → shipment lifecycle (via ADR-0003) | **Yes** |
| COD collection record | delivery | **Yes** |
| Driver/hub cash custody | delivery / hub | **Yes** (policy TBD) |
| Merchant payable recognition | finance (journal posting) | N/A — retried async |
| Wallet balance | wallet (projection from finance) | N/A — eventual |
| Settlement batch / payout execution | finance | N/A — separate workflow |

---

## Provisional chart of accounts

**[proposal]** Account codes below are **illustrative and provisional** pending business/accounting approval. Do not implement as final COA.

| Provisional account | Type | Purpose |
|---------------------|------|---------|
| `1100-DRIVER-CASH` | Asset | Cash held by delivery driver |
| `1200-HUB-CASH` | Asset | Cash held at hub |
| `1300-CASH-IN-TRANSIT` | Asset | Cash between driver and hub / bank |
| `1400-COD-RECEIVABLE` | Asset | Company claim on collected COD not yet recognized |
| `2100-MERCHANT-PAYABLE` | Liability | Amount owed to merchant (wallet projection source) |
| `2200-SETTLEMENT-CLEARING` | Liability | Funds in settlement pipeline |
| `4100-COMMISSION-REVENUE` | Revenue | HUDHUD commission on COD/shipping |
| `5100-PAYOUT-CLEARING` | Asset/Liability bridge | Payout in flight to merchant bank/cash |
| `5900-SUSPENSE-RECON` | Asset/Liability | Unmatched differences pending reconciliation |
| `6000-REFUNDS-ADJUSTMENTS` | Contra/expense | Refunds and manual adjustments |

---

## Illustrative balanced journal entries

**[proposal]** Examples only — amounts, commission, and timing are **not** approved policy.

### 1. COD collected at doorstep (driver holds cash)

| Account | Debit | Credit |
|---------|-------|--------|
| `1100-DRIVER-CASH` | 15,000 IQD | |
| `1400-COD-RECEIVABLE` | | 15,000 IQD |

*Trigger:* `CodCollected` operational fact (delivery). *Posting key:* `post:cod_collected:{shipment_id}`.

### 2. Merchant payable recognized (net of commission — formula TBD)

| Account | Debit | Credit |
|---------|-------|--------|
| `1400-COD-RECEIVABLE` | 15,000 IQD | |
| `4100-COMMISSION-REVENUE` | | 2,000 IQD |
| `2100-MERCHANT-PAYABLE` | | 13,000 IQD |

*Trigger:* `MerchantPayableRecognized` (finance policy engine). *Posting key:* `post:merchant_payable:{shipment_id}:{pricing_version}`.

*Wallet projection:* credit merchant wallet **13,000 IQD** (not gross COD) — **[unresolved policy]** commission calculation.

### 3. Driver hands cash to hub

| Account | Debit | Credit |
|---------|-------|--------|
| `1200-HUB-CASH` | 15,000 IQD | |
| `1100-DRIVER-CASH` | | 15,000 IQD |

*Trigger:* `CashHandedToHub` (hub confirms handover). *Posting key:* `post:cash_handover:{handover_id}`.

### 4. Settlement approved (batch)

| Account | Debit | Credit |
|---------|-------|--------|
| `2100-MERCHANT-PAYABLE` | 50,000 IQD | |
| `2200-SETTLEMENT-CLEARING` | | 50,000 IQD |

*Trigger:* `SettlementApproved`. *Posting key:* `post:settlement:{settlement_id}`.

### 5. Payout completed

| Account | Debit | Credit |
|---------|-------|--------|
| `2200-SETTLEMENT-CLEARING` | 50,000 IQD | |
| `5100-PAYOUT-CLEARING` | | 50,000 IQD |

*Trigger:* `PayoutCompleted`. *Posting key:* `post:payout:{payout_id}`.

### 6. Reversal (duplicate collection corrected)

| Account | Debit | Credit |
|---------|-------|--------|
| `2100-MERCHANT-PAYABLE` | 13,000 IQD | |
| `5900-SUSPENSE-RECON` | | 13,000 IQD |

*Trigger:* `FinancePostingReversed` — **new** reversal entry pair referencing original `journal_entry_id`; never delete original rows.

---

## Event semantics and posting matrix

**[proposal]** Distinguish operational integration events from finance outcomes. Event names align with `architecture/service-boundaries.yaml` where noted; finance-specific events are new proposals.

| Event | Publisher | Consumer(s) | Journal action | Idempotency key |
|-------|-----------|-------------|----------------|-----------------|
| `delivery.fact.task_completed` (`DeliveryCompleted`) | delivery | shipment, notification | None (operational) | `delivery_task_id` + outcome |
| `delivery.fact.cod_collected` (`CodCollected`) | delivery | finance, tracking | Driver cash + COD receivable (illustrative) | `cod_collection_id` |
| `shipment.fact.delivered` (`ShipmentDelivered`) | shipment | finance, notification, tracking | Merchant payable recognition trigger | `shipment_id` + aggregate version |
| `hub.fact.cash_received` (`CashHandedToHub`) | hub | finance | Hub/driver cash transfer | `handover_id` |
| `finance.fact.merchant_payable_recognized` | finance | wallet | Payable + commission split | `post:merchant_payable:{shipment_id}:{pricing_version}` |
| `finance.fact.posting_completed` | finance | wallet, audit | Confirm projection applied | `posting_id` |
| `finance.fact.posting_failed` | finance | control_tower, reconciliation | Create exception/suspense | `posting_id` |
| `finance.fact.settlement_approved` | finance | wallet, notification | Move payable to clearing | `settlement_id` |
| `finance.fact.payout_completed` | finance | wallet, notification | Close payout clearing | `payout_id` |
| `finance.fact.posting_reversed` | finance | wallet | Reversal pair | `reversal:{original_posting_id}` |

**[decision]** Physical delivery and COD collection events are **facts** (immutable). Finance posting
is a **derived accounting act** (retryable). Wallet updates occur only after finance posting
completion or approved reversal — **not** via direct Delivery→Wallet authority.

**[evidence]** Legacy `cod_collected:{shipment_id}` wallet idempotency key documents migration
evidence but is **not** the final cross-service financial posting model.

---

## Idempotency and concurrency

**[proposal]** Idempotency uses **source event identity** plus **posting keys**:

| Layer | Key format | Store |
|-------|------------|-------|
| Operational COD | `cod_collection:{shipment_id}` | delivery DB |
| Finance posting | `post:{posting_type}:{source_id}[:{pricing_version}]` | finance inbox |
| Wallet projection | `proj:{posting_id}` | wallet inbox |

**[evidence]** Legacy `cod_collected:{shipment_id}` may guide migration replay mapping to finance
posting keys but must not be treated as the authoritative cross-service model.
| Payout hold | `payout_hold:{payout_request_id}` | wallet (retain legacy pattern) |

**[proposal]** Concurrency rules:

- Delivery uses aggregate lock (`get_by_id_for_update` — legacy pattern) per shipment/task.
- Finance journal appends under transaction with unique `posting_key` constraint.
- Wallet projection consumer serializes per `merchant_id` (advisory lock or partition key).
- At-least-once delivery (ADR-0002) requires inbox dedupe before journal append.

**[evidence]** Legacy already implements wallet-level idempotency and account row locking (`record_wallet_ledger_entry.py`, `lock_by_id`).

---

## Wallet projection model

**[proposal]** Wallet service responsibilities after cutover:

| Responsibility | Owner |
|----------------|-------|
| Store-scoped wallet account registry | wallet |
| Merchant-visible ledger entries (projection) | wallet |
| Payout request intake (merchant-initiated) | wallet |
| Authoritative payable balance computation | finance → projected to wallet |
| Commission, tax, settlement math | finance |

**[proposal]** Projection flow:

1. Finance commits journal entry(ies).
2. Finance emits `FinancePostingCompleted` with `merchant_id`, `amount`, `entry_type`, `posting_id`.
3. Wallet inbox writes projected `CREDIT`/`DEBIT`/`ADJUSTMENT` with idempotency `proj:{posting_id}`.
4. Merchant API reads unchanged summary semantics (`ledger_balance`, `held_balance`, `available_balance`).

**[evidence]** Legacy `PAYOUT_HOLD` / `PAYOUT_RELEASE` zero-delta on `ledger_balance` remains valid for merchant-initiated holds until payout policy ADR extends finance legs.

---

## Reversal rather than deletion

**[proposal]** All finance and wallet projection entries are **append-only**. Corrections post compensating reversal entries referencing:

- `reverses_posting_id`
- `reverses_journal_entry_id`
- actor, reason code, authorization reference

**[evidence]** Legacy wallet already forbids ledger update/delete (`wallet_api.md`). Platform finance must not hard-delete journal rows.

**[unresolved policy]** Who may authorize reversals and whether reversals after settlement/payout require dual control.

---

## Settlement and payout boundary

**[proposal]** Boundaries:

| Stage | Scope | Legacy state |
|-------|-------|--------------|
| **Collection** | Driver records COD; delivery persists `delivery_cod_collections` | Implemented |
| **Recognition** | Finance recognizes merchant payable (net of fees) | Partially conflated with wallet CREDIT |
| **Settlement** | Batch merchant payables for a period/channel | **Not implemented** |
| **Payout** | Move funds to merchant external account | Partial — `payout_requests` + hold only; no `PAID` flow |

**[evidence]** `CreatePayoutRequestUseCase` reduces `available_balance` via hold; no bank transfer or admin approval use case exists.

**[proposal]** Settlement is a **finance workflow** (batch approval, clearing accounts). Payout is **execution** of an approved settlement line or ad-hoc payout request. Wallet `payout_requests` become intake commands to finance, not payment execution.

---

## Reconciliation process

**[proposal]** Reconciliation is finance-owned, ops-assisted:

1. **Driver cash vs COD collected** — compare sum(`CodCollected`) vs driver custody account (requires custody policy).
2. **Hub cash vs driver handovers** — match `CashHandedToHub` evidence to hub inventory.
3. **COD receivable vs merchant payables** — journal trial balance vs wallet projection checksum per merchant.
4. **Settlement vs payout** — clearing account vs bank statements (channel TBD).
5. **Suspense queue** — items from `FinancePostingFailed`, unmatched handovers, duplicate collection disputes.

**[evidence]** Legacy explicitly excludes reconciliation workflows (`store_wallet_phase_1b.md` §Deferred finance boundaries).

**[proposal]** Reconciliation differences post to `5900-SUSPENSE-RECON` until resolved; no silent ledger edits.

---

## Audit and security controls

**[proposal]**

| Control | Requirement |
|---------|-------------|
| Service identity | Finance consumers accept signed internal tokens (ADR-0004); no trust of `X-User-Id` alone |
| Least privilege | Finance DB credentials scoped to finance service only |
| Merchant wallet API | Store Owner + `ACCESS_STORE_WALLET` (legacy pattern retained) |
| Finance admin | Separate role for settlement approval, reversals, suspense resolution |
| Audit trail | Append-only audit for posting, reversal, settlement approval (extend legacy `AuditLogRepository` pattern per service) |
| PII / data class | Ledger metadata stores IDs and amounts; no unnecessary customer PII |

**[evidence]** Legacy wallet credits append `AuditAction.WALLET_LEDGER_ENTRY_RECORDED` and `DELIVERY_COD_COLLECTED`.

---

## Observability

**[proposal]** Required signals (aligned with ADR-0002 envelope fields):

| Signal | Purpose |
|--------|---------|
| `finance.posting.lag_seconds` | Time from `CodCollected` to `FinancePostingCompleted` |
| `finance.posting.failures` | Count by `posting_type`, `failure_reason` |
| `finance.suspense.open_count` | Unresolved reconciliation items |
| `wallet.projection.drift` | Hash mismatch finance payable vs wallet aggregate |
| `correlation_id` / `causation_id` / `traceparent` | Cross-service traces from delivery through finance to wallet |

**[evidence]** Legacy uses `X-Request-ID` middleware; no distributed tracing today.

---

## Migration impact

**[proposal]** One-writer cutover per datastore; **bidirectional dual-write forbidden** (`architecture/invariants.md`).

| Phase | Action |
|-------|--------|
| Extract delivery | Own `delivery_cod_collections`; publish `delivery.fact.cod_collected` via outbox |
| Stand up finance | Journal schema, inbox consumer, posting engine (blocked on policy) |
| Repoint wallet credit | Disable synchronous `CreditCodCollectedToMerchantWalletUseCase` in delivery; finance drives projection |
| Idempotency preservation | Map legacy `cod_collected:{shipment_id}` to finance posting keys for replay safety during migration |
| Payout requests | Migrate `payout_requests` to finance-owned settlement commands or retain wallet intake with finance execution |
| Credential gate | Revoke delivery/wallet cross-DB access at cutover |

**[evidence]** Legacy single PostgreSQL database — extraction requires event backfill or one-time migration scripts with provenance record if code is ported (`docs/audit/legacy-provenance.yaml`).

---

## Rollback boundaries

| Action | Reversible? | Notes |
|--------|-------------|-------|
| Physical delivery marked complete | **No** | Operational fact |
| COD collection record created | **No** | Operational fact; corrections via dispute/reversal workflow |
| Driver/hub cash handover recorded | **No** | Operational fact (when implemented) |
| Finance journal posting | Via reversal entry only | Never delete |
| Wallet projection entry | Via compensating projection | Tied to finance reversal |
| Settlement/payout sent to bank | **Policy TBD** | May require manual recovery |

**[decision]** Finance failure creates retry, exception, suspense, or reconciliation work — **not** delivery rollback.

---

## Policy decision register

**[unresolved policy]** Items below require named business/accounting deciders. **Do not implement** without approval.

| ID | Question | Blocks |
|----|----------|--------|
| P-01 | Commission calculation formula and timing | `MerchantPayableRecognized` posting |
| P-02 | Pricing version binding for payable recognition | Posting key includes `pricing_version` |
| P-03 | Who holds cash at each step (driver vs hub vs company) | Cash custody accounts and handover events |
| P-04 | Handover evidence requirements (scan, count, signature) | `CashHandedToHub` schema |
| P-05 | Settlement frequency (daily, weekly, on-demand) | Settlement batch workflow |
| P-06 | Payout approval roles and dual control | `SettlementApproved` authorization |
| P-07 | Payout channels (bank transfer, cash, mobile money) | `PayoutCompleted` integration |
| P-08 | Negative merchant balances allowed? | Wallet projection rules |
| P-09 | Refunds after settlement/payout | Reversal + clawback workflow |
| P-10 | Failed delivery with collected cash | Dispute and suspense policy |
| P-11 | Lost cash / shrinkage | Write-off accounts and approval |
| P-12 | Duplicate collection handling | Reversal vs suspense |
| P-13 | Reversal authorization matrix | `FinancePostingReversed` RBAC |
| P-14 | Accounting period close rules | Posting cutoff timestamps |
| P-15 | Retention period for finance records | Archive/compliance |
| P-16 | Tax treatment (VAT/WHT on COD/commission) | Tax lines in journal |
| P-17 | Currency scope beyond IQD | Multi-currency COA and FX policy |

---

## Dependencies

| ADR | Relationship | Status (Wave 1 integration) |
|-----|--------------|-------------------------------|
| **ADR-0002** — Event envelope, outbox/inbox, JetStream | Finance consumes `CodCollected` and `ShipmentDelivered` at-least-once with idempotent inbox | **Accepted** |
| **ADR-0003** — Shipment lifecycle authority | Delivery publishes facts; Shipment sole writer; decouples status from finance | **Accepted** |
| **ADR-0004** — Identity and service trust | Signed service credentials for finance consumers; merchant auth for wallet APIs | **Proposed** |

**[proposal]** ADR-0005 acceptance is gated on policy register P-01–P-17. ADR-0002 and ADR-0003
are accepted; ADR-0004 must be accepted before production finance endpoints.

---

## Implementation blockers

1. **[unresolved policy]** Policy register P-01 through P-17.
2. **[decision]** ADR-0002 **Accepted** — inbox/outbox contract defined; finance consumer implementation blocked on policy.
3. **[decision]** ADR-0003 **Accepted** — shipment lifecycle fact boundary defined.
4. **[evidence]** ADR-0004 **Proposed** — service-to-service auth for finance unsettled.
5. **[proposal]** Chart of accounts and commission engine not approved.
6. **[proposal]** No finance service bootstrap (explicit non-action for this workstream).

---

## Consequences

### Positive

- Clear separation of operational facts vs accounting vs merchant UI projection.
- Aligns with platform invariants on irreversible delivery and finance retry.
- Double-entry enables reconciliation, commission, and audit trails when policy arrives.
- Preserves legacy idempotency patterns where compatible.

### Negative

- Eventual consistency for merchant wallet after delivery (UX change from legacy synchronous credit).
- Higher operational complexity: suspense, reconciliation queues, projection drift monitoring.
- Requires finance service, journal schema, and migration from monolith coupling.

### Neutral

- Legacy payout request API may remain merchant intake surface with finance backend.
- IQD-only wallet remains default until P-17 resolved.

---

## Unresolved questions

- See **Policy decision register** (P-01–P-17).
- Should ops `MarkShipmentDelivered` ever trigger payable recognition without driver COD evidence?
- How are claim/refund modules (`claims/`) integrated with finance reversals?
- Is `wallet_cod` merged with `finance_settlement` deployable initially or strictly separate services?

---

## Alternatives considered

| Alternative | Why not recommended as final state |
|-------------|-----------------------------------|
| Continue legacy synchronous wallet credit in delivery transaction | Violates finance/delivery rollback invariant; blocks service extraction |
| Single-entry operational ledger only | Insufficient for commission, custody, and reconciliation without ad-hoc balancing |
| Wallet as authoritative GL | Conflates merchant UI with company books; no driver/hub cash legs |
| Exact-once messaging | Contradicts platform at-least-once JetStream invariant |

---

## References

- Platform: `architecture/invariants.md`, `architecture/service-boundaries.yaml`, `architecture/ownership-matrix.yaml`
- Platform audit: `docs/audit/legacy-data-ownership-inventory.md`, `docs/audit/legacy-domain-inventory.md`
- Legacy evidence SHA: `2e375057fdf9b9ce8416408a4436303be5301def`
- Legacy docs: `docs/production_v1/store_wallet_phase_1b.md`, `docs/api/wallet_api.md`, `docs/audits/phase15_1_merchant_cod_balance_integrity.md`
- Related ADRs: ADR-0002 (eventing, Accepted), ADR-0003 (shipment authority, Accepted), ADR-0004 (identity trust, Proposed)
- Template: `docs/adr/0000-template.md`
