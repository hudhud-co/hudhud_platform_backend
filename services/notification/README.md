# HUDHUD Notification Service

Bounded context(s) owned: `notification` (ADR-0011).

Notification dispatch across app, SMS and WhatsApp channels, with per-channel delivery attempts.

This service owns its own database, migrations, contracts and tests. It never reads another
service's tables and never imports another service's Python package.

## Status

Bootstrap domain foundation. `production_ready: false` — see
`architecture/ownership-matrix.yaml`.

## Validation

```bash
cd services/notification && uv run pytest
uv run ruff check .
```

## Audit

Requirements, evidence and gap analysis: `docs/audits/hudhud-app-redesign-v6.3/`.
