# HUDHUD Customer Service

Bounded context(s) owned: `customer, address_book` (ADR-0011).

Customer profile, legal-document acceptance, notification preferences and the address book.

This service owns its own database, migrations, contracts and tests. It never reads another
service's tables and never imports another service's Python package.

## Status

Bootstrap domain foundation. `production_ready: false` — see
`architecture/ownership-matrix.yaml`.

## Validation

```bash
cd services/customer && uv run pytest
uv run ruff check .
```

## Audit

Requirements, evidence and gap analysis: `docs/audits/hudhud-app-redesign-v6.3/`.
