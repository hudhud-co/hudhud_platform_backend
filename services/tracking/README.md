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

## Query HTTP API

```http
GET /tracking/shipments/{shipment_id}/timeline
Authorization: Bearer <token>
```

Query parameters: `limit` (bounded), optional opaque `cursor`.

Response includes `shipment_id`, ordered `entries`, and `next_cursor` when another page
exists. Ordering is **display order** (`occurred_at` + `event_id`), not authoritative
commit ordering.

### Safe response boundary

HTTP responses expose only presentation fields:

- `event_id`, `occurred_at`, `legacy_event_type`, `previous_status`, `new_status`

Excluded from HTTP: source position/system/table/pk, Bridge mapper version, actor ID,
raw metadata, JetStream metadata, inbox state, and processing/error information.

### Authorization port

Timeline reads require `Authorization: Bearer <token>`. Access is decided only by the
service-owned `TrackingQueryAuthorizer` port — `X-User-Id`, `X-Role`, and similar
identity headers never grant access.

The default composition-root authorizer fails closed (all reads rejected). Production
readiness remains blocked until a real authorization adapter is configured.

**ADR-0004** (JWT/JWKS identity and service trust) is **not implemented** in this Wave.
A production-ready JWT/JWKS authorizer adapter is a **production blocker**.

| Condition | HTTP status |
|-----------|-------------|
| Missing/malformed bearer | `401` |
| Authorizer rejects token | `401` |
| Authenticated but not authorized for shipment | `403` |
| Invalid shipment ID, cursor, or limit | `422` |
| Authorizer or query dependency unavailable | `503` |

Pagination uses a versioned, URL-safe opaque cursor scoped to the requested shipment.
Malformed or mismatched cursors return `422`. Raw bearer tokens are never logged,
returned, or stored.

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

- ADR-0004 JWT/JWKS authorizer adapter for timeline query API
- ADR-0010 service-to-service NATS credentials and TLS in production/staging
- PostgreSQL migration runtime proof (disposable lab)
- Secured JetStream consumer runtime proof (disposable lab)
- Production/staging database credentials and network isolation
