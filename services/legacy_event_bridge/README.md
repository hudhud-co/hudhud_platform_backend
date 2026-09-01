# Legacy Event Bridge

Transitional technical deployable that durably captures allowlisted legacy append-only
rows and publishes ADR-0009 observation events through a Bridge-owned transactional outbox.

This service is **not** a bounded context. It owns Bridge-local landing, checkpoint, and
outbox persistence only.

## Pipeline

```text
CDC adapter boundary
  → normalized durable landing
  → durable checkpoint
  → A1/A2 observation mapper
  → Bridge-owned transactional outbox
  → publisher boundary
```

## Scope

- A1: `legacy_bridge.observation.shipment_timeline_entry`
- A2: `legacy_bridge.observation.audit_entry`

## Runtime

```bash
cd services/legacy_event_bridge
uv sync
uv run uvicorn legacy_event_bridge.main:app --host 127.0.0.1 --port 8095
```

Production startup remains blocked until ADR-0004 service credentials and ADR-0007 staging
gates are explicitly satisfied in configuration.

Outbox publish retry delays default to `5s → 30s → 120s → 600s → 1800s` via
`LEGACY_BRIDGE_OUTBOX_RETRY_BACKOFF_SECONDS` (comma-separated). These values are
**provisional** and not accepted production policy.

Stored outbox JSON is canonicalized to deterministic UTF-8 bytes at publish time
(`sort_keys=True`, compact separators). Original producer whitespace/order is not
preserved when JSONB storage has already normalized the envelope dict.

## Validation

```bash
uv run ruff check .
uv run pytest -q
uv run python ../../scripts/quality/verify_boundaries.py
```
