# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs) for the HUDHUD platform backend.

## Process

1. Copy `0000-template.md` to `NNNN-short-title.md` with the next sequential number.
2. Fill in context, decision, and consequences.
3. Link related ADRs and update `architecture/service-boundaries.yaml` when a decision
   affects bounded context ownership or deployable boundaries.

## Status Values

- **proposed** — under discussion
- **accepted** — binding for implementation
- **deprecated** — superseded but retained for history
- **superseded by ADR-NNNN** — replaced by a newer decision

## Relationship to Legacy

Legacy design docs in `hudhud-backend` are reference material only. Platform ADRs in this
directory are authoritative for `hudhud_platform_backend`.
