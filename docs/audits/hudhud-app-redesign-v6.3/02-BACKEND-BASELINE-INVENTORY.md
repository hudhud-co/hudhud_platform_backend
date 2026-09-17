# 02 — Backend Baseline Inventory

Captured from the implementation, not from README files.

## Baseline (Phase 0)

| Item | Value |
|------|-------|
| Branch | `develop` |
| HEAD | `573eac12d5f70b829667c00a3f724a3b623ac3eb` |
| Working tree at start | 87 entries — 47 modified, 40 untracked, all from the **immediately preceding Driver/Pickup parity wave** (uncommitted). Preserved untouched; this audit builds on them. |
| Pre-existing dirty files outside that wave | `docs/input/` (the three product sources) — untracked, read-only input |

### Baseline validation (recorded before any change)

```
uv run ruff check .                                   → All checks passed!
uv run python scripts/quality/verify_boundaries.py    → Architecture boundary verification passed.
uv run python scripts/quality/verify_agent_governance.py → Agent governance verification passed.
services/pickup              → 259 passed
services/shipment            → 146 passed
services/tracking            → 129 passed
services/audit               →  81 passed
services/legacy_event_bridge →  59 passed
tests/architecture tests/governance tests/contracts → 101 passed
                                              TOTAL → 775 passed, 0 failed
```

Pre-existing failure, **not** caused by this work:
`tests/service_postgres_proof/test_shipment_integration.py::test_shipment_http_acceptance_against_postgres`
— "staging startup blocked — unset gates: acceptance_ingestion_mode". The probe was last updated in
`dbf873b`; the gate it trips was added later by `70597ca`.

## Deployed services (the only code that exists)

| Service | Bounded context | Extraction status | DB | Events |
|---------|-----------------|-------------------|----|--------|
| `services/pickup` | `pickup` | `bootstrap_domain_foundation` | own (3 migrations, head `w19a_pickup_driver_wave_001`) | **produces** `pickup.fact.accepted`, `pickup.fact.handover_completed` (transactional outbox) |
| `services/shipment` | `shipment` | `bootstrap_domain_foundation` | own (5 migrations, head `w19b_hub_custody_transfer_001`) | **consumes** both pickup facts (durable idempotent inbox) |
| `services/tracking` | `tracking` | `bootstrap_observation_projection` | own (1 migration) | consumes `legacy_bridge.observation.shipment_timeline_entry` |
| `services/audit` | `audit` | `bootstrap_integrated` | own (1 migration) | consumes `legacy_bridge.observation.audit_entry` |
| `services/legacy_event_bridge` | *transitional technical deployable — not a bounded context* | `bootstrap_integrated` | landing/outbox only | publishes the two observation contracts |

`packages/`: `event_envelope`, `messaging_conformance` (allowlisted technical primitives only).

## Contexts with **no service** in this repository

`auth_identity` (`pending_identity_adr`) · `customer` (`policy_blocked`) · `address_book` ·
`merchant_store` · `serviceability` · `pricing_quote` · `order` · `send_parcel` · `hub` ·
`linehaul` (all `pending_legacy_audit`) · `delivery` (`pending_cutover`) · `wallet_cod`
(`policy_blocked`) · `finance_settlement` (`not_started`, `transitional_deployable_candidate:
policy_blocked`) · `notification` · `support_claims` · `media_proof` · `control_tower` ·
`gateway` (`pending_wave_0`).

## Pickup service — actual surface

### HTTP (29 driver endpoints + 4 recovery + 2 health)

- Recovery (`/pickup/tasks`): `POST /{id}/retry`, `/reschedule`, `/reassign`, `/cancel`
- Work sessions (`/pickup`): `POST /work-sessions/start`, `/{id}/pause`, `/{id}/resume`, `/{id}/end`, `GET /work-sessions/current`
- Task lifecycle (`/pickup/tasks/{id}`): `POST /acknowledge`, `/decline`, `/arrive`, `/scan`, `/condition-proof`, `/exception`, `/fail`, `/accept`; `GET /history`
- Handover ceremony + hub handover (`/pickup`): 10 routes (challenge issue/verify, manifest submit/confirm, handover manifest create/arrive/cancel/receipt/close, read)
- Offline (`/pickup/offline`): authorization issue/revoke, `POST /sync`, reconciliation cases list/resolve
- Health: `GET /health`, `GET /ready`

### Domain

`PickupTaskStatus` = PENDING · ARRIVED · SCANNED · PROOF_CAPTURED · EXCEPTION_REPORTED · FAILED ·
SUPERSEDED · CANCELLED, with forward-only `PICKUP_TASK_PROGRESSION`.
`AssignmentState` = OFFERED · ACKNOWLEDGED · DECLINED.
`AssignmentDeclineReason` (7) · `PickupExceptionReason` (12) · `PackageConditionStatus` = GOOD ·
MINOR_DAMAGE · MAJOR_DAMAGE · REPACKAGED · `PickupTaskAcceptanceState` · `AcceptanceOutcome` ·
`CustodyType` = PICKUP_DRIVER · ORIGIN_HUB.
`PickupTask` is **one task per shipment** (`shipment_id`), grouped into a driver stop by
`assigned_batch_id`.
Also: `DriverWorkSession` + blockers, courier challenge/manifest, handover manifest + items,
offline authorization/stream/event/reconciliation case.

### Application services

`PickupRecoveryService` · `PickupAcceptanceService` (outbox + acceptance gate) ·
`DriverWorkSessionService` · `PickupTaskLifecycleService` (+ `for_replay()`) ·
`CourierHandoverVerificationService` · `HubHandoverService` · `OfflineSyncService` ·
`OfflineOperationDispatcher` · `collect_driver_workload` · `record_history`.

### Ports

`ShipmentEligibilityPort` (custody_type only), `PickupAuthorizer` (25 `PickupCommand` values,
5 `PickupRole`), `AcceptanceVerificationGate`, `RecoveryAuthorizer`. Production identity and
shipment adapters are **deferred / default-deny**.

## Shipment service — actual surface

- HTTP: `POST /v1/shipments/...` acceptance (compatibility-internal), `GET /health`, `GET /ready`
- Sole canonical writer of shipment lifecycle (ADR-0003)
- Consumes `pickup.fact.accepted` → custody `PICKUP_DRIVER`; `pickup.fact.handover_completed` →
  custody transfer to `ORIGIN_HUB` (`PickupHandoverCustodyApplyService`)
- Durable inbox keyed `(consumer_name, event_id)`; poison handling; `CustodyTransferRecord`

## Cross-cutting mechanisms available for reuse

Transactional outbox + durable idempotent inbox (ADR-0008) · versioned JSON-Schema event contracts
under `contracts/events/` with `registry.yaml` (ADR-0009) · NATS/JetStream subject allowlist and
per-service identities (ADR-0010) · optimistic concurrency `version` + `SELECT … FOR UPDATE` +
partial unique indexes · idempotency keys + command fingerprints · savepoints for offline replay ·
HMAC-SHA256 keyed hashing with constant-time compare · structured error → HTTP mapping ·
readiness gates · disposable PostgreSQL/JetStream Docker labs.

## Toolchain

```
uv run ruff check .
uv run python scripts/quality/verify_boundaries.py
uv run python scripts/quality/verify_agent_governance.py
uv run pytest tests/architecture tests/governance tests/contracts
(cd services/<svc> && uv run pytest)
tests/service_postgres_proof · tests/pickup_acceptance_eventing_proof · tests/nats_security_proof  (Docker labs)
```
