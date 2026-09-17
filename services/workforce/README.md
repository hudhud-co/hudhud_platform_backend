# HUDHUD Workforce Service

Bounded context owned: `driver_workforce` (ADR-0013).

Driver onboarding and physical office verification, working-hours patterns, shift
attendance, lateness blocks, leave requests, and the assignment-eligibility answer that
Pickup and Delivery both depend on.

This service owns its own database, migrations and tests. It resolves callers by asking
Identity over HTTP, and never imports another service's package or reads its tables.

## Why this is a service

`pickup` assigns pickup batches and `delivery` assigns last-mile manifests, and both need
the same answer — *may this driver be given work right now?* That answer depends on facts
neither of them owns: whether the office check happened, whether a lateness block is open,
whether the driver is on leave, and whether they have a shift today. Putting it inside
`pickup` would make `delivery` depend on `pickup` for something with no parcel in it.

## Requirements covered

| ID | Requirement |
|----|-------------|
| SEC-03 | Applicant submits identity, vehicle, shifts and terms; the account starts pending; **physical office verification is required before any task assignment** |
| DRV-A01 | Starting a shift records attendance; assigned work unlocks on start |
| DRV-A02 | A late start is recorded and can temporarily block assignment; support can clear it |
| DRV-A03 | Leave request, full-day or hourly, with reason and note; no lateness penalty while pending |
| DRV-A04 | Working-hours management; changes apply from tomorrow |
| OPS-09 | Support clears lateness blocks and decides leave requests |

## The gate

A remote submission never produces an assignable driver. `AWAITING_OFFICE_VERIFICATION` is
the only route to `VERIFIED`, only Operations can record it, and the document checklist —
ID card, driving licence, vehicle registration, insurance certificate — must be complete.
The database agrees: a verified application without a named operator and office is
rejected by a check constraint, and a `DriverProfile` row exists only because one was
verified.

## What is configuration, not code

v6.3 fixes neither a lateness grace period nor a blocking threshold, and the Driver App
shows only one worked example (a 52-minute delay). Both are settings with explicit
defaults rather than constants, because a guessed threshold penalises real drivers.

## Two rules worth stating

**"Changes apply from tomorrow."** A new shift pattern takes effect the following day, and
the outgoing pattern is closed at the *end of today* rather than at the moment of the
change — otherwise a driver who edits their hours at lunchtime retroactively loses this
morning's shift, and the attendance they already recorded becomes unexplainable.

**"No lateness penalty applies while your request is pending."** A leave request protects
the driver from the moment it is raised, before anyone has decided anything, so
`protects_from_lateness` covers `PENDING` as well as `APPROVED`.

## Events

None. Workforce answers questions over HTTP and publishes no integration events, so it
owns a durable inbox and no outbox. A boundary test asserts that stays true.

## Validation

```bash
uv run --project services/workforce pytest services/workforce/tests   # 119 tests
uv run ruff check services/workforce
uv run pytest tests/new_service_migration_proof -m integration        # real PostgreSQL 16
```

## Audit

Requirements, evidence and gap analysis: `docs/audits/hudhud-app-redesign-v6.3/`.
