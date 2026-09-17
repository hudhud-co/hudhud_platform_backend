# HUDHUD Hub Service

Bounded context(s) owned: `hub, linehaul` (ADR-0011).

Hub intake and drop-off, sorting, parcel groups and optional seals, and inter-city linehaul custody.

This service owns its own database, migrations, contracts and tests. It never reads another
service's tables and never imports another service's Python package.

## Status

Bootstrap domain foundation. `production_ready: false` — see
`architecture/ownership-matrix.yaml`.

## Validation

```bash
cd services/hub && uv run pytest
uv run ruff check .
```

## Audit

Requirements, evidence and gap analysis: `docs/audits/hudhud-app-redesign-v6.3/`.
