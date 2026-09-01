# Audit

Independently deployable HUDHUD Audit service. This Wave bootstraps a **Legacy A2
observation consumer** for durable searchable projection and migration evidence.

This service does **not** create native canonical Audit facts
(`audit.fact.entry_recorded` is out of scope). A2 observations remain
`producer=legacy_bridge` integration messages.

## Ingestion path

```text
JetStream delivery boundary
    → envelope/contract validation
    → service-owned inbox
    → Audit observation projection
    → commit
    → ACK decision
```

## Accepted input (ADR-0009 A2)

| Field | Value |
|-------|-------|
| Event type | `legacy_bridge.observation.audit_entry` |
| Event version | `1` |
| Subject | `hudhud.audit.legacy_bridge.observation.audit_entry.v1` |
| Stream | `HUDHUD_AUDIT` |
| Durable consumer | `audit_bridge_entry_v1` |
| Producer | `legacy_bridge` |
| Message kind | `integration` |
| Aggregate scope | `non_aggregate` |

Mismatched producer, type/version, subject, source table, or aggregate fields are
rejected and quarantined. Secret-like metadata is not stored.

## Persistence

Service-owned PostgreSQL (dedicated database strategy):

- `audit_integration_inbox` — ADR-0008 inbox, unique on `(consumer_name, event_id)`
- `legacy_audit_observations` — normalized A2 projection (not a canonical fact)

`Nats-Msg-Id` is optional transport provenance only. Inbox uniqueness is
authoritative.

## Runtime

```bash
cd services/audit
uv sync
uv run uvicorn audit.main:app --host 127.0.0.1 --port 8096
```

Health routes (`/health`, `/ready`) are composition-root liveness only. This Wave
does not expose a business HTTP API.

Production NATS credentials remain blocked by ADR-0004. A local JetStream adapter
requires explicit configuration, embeds no credentials, preserves ACK-after-commit,
explicit NAK on unexpected handler errors, and is not started by tests or the
default app factory.

## Validation

```bash
uv lock --check
uv run ruff check .
uv run pytest -q
uv run python ../../scripts/quality/verify_boundaries.py
```

Do not run Docker, NATS, or PostgreSQL for this Wave's targeted tests.

## Remaining gates

- ADR-0004 service-to-service NATS credentials
- Production/staging database credentials and network isolation
- Live-environment JetStream consumer proof (local disposable PostgreSQL proven in Wave 6)
- Compose/CI registration (integration wave)
- Native `audit.fact.entry_recorded` after Audit cutover
