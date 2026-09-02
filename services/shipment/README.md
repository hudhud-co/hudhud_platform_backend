# Shipment

Independently managed HUDHUD Shipment service package. This Wave establishes the
**order intent → acceptance scan** domain foundation only. Shipment remains the sole
canonical writer of shipment lifecycle state (ADR-0003), but this Wave implements
acceptance-boundary behavior without HTTP, PostgreSQL, NATS, or post-acceptance
lifecycle stages.

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

## Application API (in-process)

`AcceptanceLifecycleService` exposes:

- `create_order_intent` — creates `OrderIntent` + `Shipment` (`CREATED`) without custody/SLA.
- `register_pickup_task` — persists service-local Pickup prerequisite snapshot.
- `record_acceptance_scan` — validates Phase 11 prerequisites and applies effects atomically.

Unit of work port: `AcceptanceUnitOfWork`. Tests use `InMemoryAcceptanceUnitOfWork`
(copy-on-write rollback).

## Explicit non-goals (deferred)

- **Production Pickup integration adapter** — live Pickup service/database/API reads
- Delivery / Delivered and all post-acceptance lifecycle stages
- Hub/bag/manifest/seal/linehaul operations
- Linehaul, payments/COD/settlement/refunds, returns
- HTTP endpoints, PostgreSQL migrations, NATS/events, authentication
- Cross-service distributed transaction claims

## Validation

```bash
cd services/shipment
git diff --check
uv lock --check
uv run ruff check .
uv run pytest -q
uv run python ../../scripts/quality/verify_boundaries.py
```

W11 evidence is **unit/in-memory only** — no Docker, network, NATS, PostgreSQL, or
legacy repository access in targeted tests.

## Production readiness

**Not production-ready.** Runtime persistence, API surface, secured messaging, Pickup
integration, and post-acceptance lifecycle remain future Waves.
