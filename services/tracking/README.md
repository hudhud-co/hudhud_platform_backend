# Tracking

Independently deployable HUDHUD Tracking service. This Wave bootstraps a **Legacy A1
timeline observation consumer** for durable read projection and migration evidence.

This service does **not** create canonical Shipment lifecycle facts. A1 observations
remain `producer=legacy_bridge` integration messages and do not confer Tracking or
Shipment write authority.

## Ingestion path

```text
JetStream delivery boundary
    → envelope/contract validation
    → service-owned inbox
    → shipment timeline projection
    → commit
    → ACK decision
```

## Accepted input (ADR-0009 A1)

| Field | Value |
|-------|-------|
| Event type | `legacy_bridge.observation.shipment_timeline_entry` |
| Event version | `1` |
| Subject | `hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1` |
| Stream | `HUDHUD_SHIPMENT` |
| Durable consumer | `tracking_bridge_timeline_v1` |
| Producer | `legacy_bridge` |
| Message kind | `integration` |
| Aggregate scope | `non_aggregate` |

Mismatched producer, type/version, subject, source table, or aggregate fields are
rejected and quarantined. Secret-like metadata is not stored.

## Persistence

Service-owned PostgreSQL (dedicated database strategy):

- `tracking_integration_inbox` — ADR-0008 inbox, unique on `(consumer_name, event_id)`
- `shipment_timeline_entries` — normalized A1 projection (not Shipment authority)

Display ordering uses `(occurred_at, event_id)` and is **not** commit ordering.
`(occurred_at, source_pk/event_id)` does **not** prove gap-free capture.

`Nats-Msg-Id` is optional transport provenance only. Inbox uniqueness is authoritative.

## Query application layer

Typed read ports (`get_by_event_id`, `list_by_shipment_id`, deterministic cursor
pagination) are available for a future authenticated HTTP/gateway adapter. This Wave
exposes `/health` and `/ready` only.

## Runtime

```bash
cd services/tracking
uv sync
uv run uvicorn tracking.main:app --host 127.0.0.1 --port 8097
```

Production NATS credentials remain blocked by ADR-0010. A local JetStream adapter
requires explicit configuration, embeds no credentials, preserves ACK-after-commit,
explicit NAK on unexpected handler errors, and is not started by tests or the default
app factory.

## Validation

```bash
uv lock --check
uv run ruff check .
uv run pytest -q
uv run python ../../scripts/quality/verify_boundaries.py
```

Wave 8-A evidence is **unit/fake only** — no live PostgreSQL, NATS, or Docker proof in
this service's targeted tests.

## Remaining gates

- ADR-0010 service-to-service NATS credentials and TLS in production/staging
- PostgreSQL migration runtime proof (disposable lab)
- Secured JetStream consumer runtime proof (disposable lab)
- Production/staging database credentials and network isolation
- Authenticated HTTP/gateway query API
