# Shipment

Independently managed HUDHUD Shipment service package. This Wave establishes the
**order intent → acceptance scan** domain foundation only. Shipment remains the sole
canonical writer of shipment lifecycle state (ADR-0003), but this Wave implements
acceptance-boundary behavior without HTTP, PostgreSQL, NATS, or post-acceptance
lifecycle stages.

## Source authority

| Document | Version | Sections implemented in W11 |
|----------|---------|----------------------------|
| `docs/source/hodhod_comprehensive_analysis_en_v1_4.pdf` — *Hodhod Comprehensive Product, Operations & Policy Analysis* | v1.4 (2026-05-31) | §2 Core Operating Principles; §3 Full Parcel Journey Model; §5 Pickup and Acceptance Scan; §6 Photo Evidence and Metadata Policy; §7 Photo Capture Checkpoints; §33 Confirmed Decisions Table; §34 Conceptual Entities and Domains |

Later management shipment documents are **not** substitutes for this source.

## Domain design

```text
OrderIntent (intent only)
    └── Shipment aggregate
            ├── pre-acceptance: no Hodhod custody, no SLA clock
            └── AcceptanceDecisionRecord (traceable internal record)
                    ├── waybill/shipment identity
                    ├── scan timestamp + responsible operator
                    ├── packaging/seal assessment
                    ├── optional approximate weight/dimensions
                    ├── parcel-condition evidence references
                    ├── exception evidence (accepted-with-exception)
                    └── acceptance outcome
```

### Custody and SLA invariants

| Event | Hodhod network entry | Custody start | SLA start |
|-------|---------------------|---------------|-----------|
| Order creation (intent) | No | No | No |
| Acceptance — accepted | Yes | Scan timestamp | Scan timestamp |
| Acceptance — accepted-with-exception | Yes (exception preserved) | Scan timestamp | Scan timestamp |
| Acceptance — rejected | No | No | No |

### Evidence quality

- Evidence is stored as **external references only** (`storage_uri`); inline media
  bytes and data URLs are rejected.
- Missing capture timestamp and/or location metadata does **not** discard the
  photo reference; it is retained with `low_trust=True` and explicit reasons.

## Application API (in-process)

`AcceptanceLifecycleService` exposes:

- `create_order_intent` — creates `OrderIntent` + `Shipment` without custody/SLA.
- `record_acceptance_scan` — records `AcceptanceDecisionRecord` and applies outcome rules.

Repository port: `ShipmentRepository`. Tests use `InMemoryShipmentRepository`.

## Explicit non-goals (deferred)

- Delivery / Delivered and all post-acceptance lifecycle stages
- Pickup assignment, hub/bag/manifest/seal operations beyond acceptance assessment
- Linehaul, payments/COD/settlement/refunds, returns
- HTTP endpoints, PostgreSQL migrations, NATS/events, authentication
- Cross-service calls and generic workflow frameworks

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

**Not production-ready.** Runtime persistence, API surface, secured messaging, and
post-acceptance lifecycle remain future Waves.
