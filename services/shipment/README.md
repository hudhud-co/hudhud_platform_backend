# Shipment

Independently managed HUDHUD Shipment service. Shipment remains the sole canonical
writer of shipment lifecycle state (ADR-0003). This package covers the
**order intent → acceptance scan** domain foundation (W11), PostgreSQL persistence
(W15-A), and the acceptance command HTTP API (W16-A).

## Source authority

| Document | Version | Sections implemented in W11 |
|----------|---------|----------------------------|
| `hodhod_comprehensive_analysis_en_v1_4.pdf` — *Hodhod Comprehensive Product, Operations & Policy Analysis* | v1.4 (2026-05-31) | §2 Core Operating Principles; §3 Full Parcel Journey Model; §5 Pickup and Acceptance Scan; §6 Photo Evidence and Metadata Policy; §7 Photo Capture Checkpoints; §33 Confirmed Decisions Table; §34 Conceptual Entities and Domains |

Later management shipment documents are **not** substitutes for this source.
See `docs/source/README.md` for provenance (PDF not in Git).

## Domain design

```text
OrderIntent (intent only)
    └── Shipment aggregate (current_status=CREATED until acceptance)
            ├── PickupTaskSnapshot (service-local prerequisite input; Pickup adapter deferred)
            ├── ShipmentEvent append (ACCEPTANCE_SCAN on successful acceptance)
            └── AuditLogEntry append
```

### Phase 11 acceptance prerequisites

Acceptance is allowed only when all of the following hold:

1. Pickup task status is `PROOF_CAPTURED`
2. Pickup task has an assigned driver
3. Pickup task has an assigned batch
4. The acting driver is the assigned driver
5. Shipment status is `CREATED`
6. Pickup-condition proof exists
7. The scanned shipment/waybill code matches the expected shipment identifier

Each violated prerequisite raises an explicit domain error — no silent generic rejection.

### Successful acceptance effects (atomic UoW)

| Field / effect | Value |
|----------------|-------|
| `shipment.current_status` | `IN_CUSTODY` |
| `shipment.accepted_at` | acceptance scan timestamp |
| `shipment.sla_started_at` | acceptance scan timestamp |
| `shipment.current_custody_type` | `DRIVER` |
| `shipment.current_custody_id` | assigned driver user id |
| Pickup task acceptance state | `ACCEPTED` or `ACCEPTED_WITH_EXCEPTION` |
| ShipmentEvent | `ACCEPTANCE_SCAN`, `CREATED` → `IN_CUSTODY` |
| AuditLog | append acceptance decision |

Rejected scans (source §5) record pickup rejection and audit only — no custody, SLA,
or `ACCEPTANCE_SCAN` event. Accepted-with-exception (source §5) requires exception
evidence references and otherwise follows the successful acceptance path.

### Evidence quality

- Evidence is stored as **external references only** (`storage_uri`); inline media
  bytes and data URLs are rejected.
- Missing capture timestamp and/or location metadata does **not** discard the
  photo reference; it is retained with `low_trust=True` and explicit reasons.

## Persistence (W15-A)

- Service-owned Alembic history under `alembic/` (single head).
- `SqlAlchemyAcceptanceUnitOfWork` is **async-native** (no `asyncio.run` /
  `run_until_complete` / manual loop bridging from request handlers).
- Optimistic concurrency via aggregate `version` columns.
- Disposable PostgreSQL upgrade and transaction/rollback proof exist in the
  platform lab (`infra/labs/service-postgres-proof` / W15 evidence).

## HTTP API (W16-A)

Composition root: `shipment.main:create_app`.

| Route | Role |
|-------|------|
| `GET /health` | Liveness only |
| `GET /ready` | Configuration, PostgreSQL reachability, authorization-adapter readiness |
| `POST /v1/shipments/{shipment_id}/acceptance-scans` | Acceptance-scan command |

Command requirements:

- `Authorization: Bearer …` — identity from the injected authorization port only
- `Idempotency-Key` — required; matching fingerprint replays; conflict → 409
- Actor identity is **never** taken from `X-User-Id`, `X-Role`, request body, or query
- Default production authorizer is **default-deny / not production-ready**
- Tests inject `FakeAcceptanceAuthorizer`
- Response is returned only after DB commit; rollback leaves no partial acceptance
- ACK/outbox/NATS events remain out of scope

Configuration: `DATABASE_URL` (or `SHIPMENT_DATABASE_URL`), `SHIPMENT_ENVIRONMENT`.

## Explicit non-goals (deferred)

- Production identity/JWT authorization adapter (beyond default-deny + test fake)
- Production Pickup integration adapter
- Delivery / post-acceptance lifecycle stages
- Hub/bag/manifest/seal/linehaul operations
- Payments/COD/settlement/refunds, returns
- NATS/outbox ACK publishing
- Production/staging deployment and secured runtime

## Validation

```bash
cd services/shipment
git diff --check
uv lock --check
uv run ruff check .
uv run pytest -q
uv run python ../../scripts/quality/verify_boundaries.py
```

Service-local tests are unit/in-memory (and static adapter checks). Disposable
PostgreSQL + FastAPI ASGI proof lives in `tests/service_postgres_proof`
(`workflow_dispatch` only). No NATS or legacy repository access in that lab.

## Production readiness

**Not production-ready.** PostgreSQL/Alembic, disposable lab persistence proof
(W15), and local disposable HTTP+PostgreSQL command-API proof (W16) exist. Default
authorization remains fail-closed. Production identity adapters, secured messaging,
Pickup integration, and post-acceptance lifecycle remain deferred.
