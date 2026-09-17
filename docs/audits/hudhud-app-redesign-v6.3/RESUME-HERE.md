# Resume point — platform build-out

Everything in the repository is green at this point. Nothing is half-applied.

```
3,069 tests passing · 0 failing suites
  2,353 service tests · 716 root-suite tests, all four Docker labs in ONE session
106 live-API journey steps pass against the running platform · 0 failed
ruff · boundaries · governance · accounting · traceability · port registry — all pass
```

Start the platform and call its APIs:

```bash
uv run python scripts/dev/stack.py up        # 14 services, a database each
uv run python scripts/dev/journeys.py        # drive the real HTTP APIs
```

See `12-RUNNING-THE-PLATFORM.md` for the five integration defects that running it found —
each one lived between services, and every unit test passed over all of them.

## Requirement accounting

`requirement-accounting.yaml` is the source of truth and is machine-checked by
`scripts/quality/verify_requirement_accounting.py`, which fails if any catalogued
requirement is missing, duplicated, or carries a status it cannot support.

The catalogue holds **160** requirements, not the 140 quoted in earlier documents. The
difference is traced and machine-checked in `11-REQUIREMENT-TRACEABILITY.md`: the
catalogue has always held 160 unique rows, and the 140 is the sum of the six status
buckets in `04-GAP-AND-CHANGE-MATRIX.md` §Summary. Twenty catalogued requirements were
never given a status there. **No requirement was added.**

| Status | Count |
|--------|-------|
| `COMPLETE` | 149 |
| `PLANNED` | 4 |
| `BLOCKED_BUSINESS_DECISION` | 5 |
| `SOURCE_CONFLICT` | 2 |

The two tables in `11-REQUIREMENT-TRACEABILITY.md` are now **generated** from the
accounting, and the verifier fails when they disagree with it. They had drifted: seven
claims requirements still read `planned` after they were implemented. Rewrite them with
`verify_requirement_traceability.py --write`.

## Done

| Wave | Service | Tests | Migration proven on PostgreSQL 16 |
|------|---------|-------|------------------------------------|
| W20-A | `identity` | 85 | not yet |
| W20-B | `customer` | 67 | not yet |
| W21 | `merchant` | 205 | yes |
| W22 | `ordering` | 222 | yes |
| W23 | `hub` | 140 | yes |
| W24 | `notification` | 110 | yes |
| W25 | `workforce` | 121 | yes |
| W26 | `delivery` | 217 | yes, **and the real unit of work too** |
| W27 | `finance` | 252 | yes, **and the real unit of work too** |
| W28 | `claims` | 164 | yes, **and the real unit of work too** |

Pre-existing and revalidated every sweep: `pickup` 355, `shipment` 146, `tracking` 129,
`audit` 81, `legacy_event_bridge` 59. Root suites: 716.

### What W28 `claims` delivered

CLM-01 … CLM-07, SEC-07, DRV-P25, OPS-06 and OPS-07, complete through every layer:
domain, application, PostgreSQL persistence (`w28_claims_core_001`), a 17-path HTTP
surface, Identity-backed authorization, and proofs on a real database. Running on
`http://127.0.0.1:8114`; `services/claims/README.md` explains the design decisions.

Four things in it are worth knowing before changing it:

* **Who may open a claim and who gets paid are different questions.** v6.3 p.42 lets the
  sender, receiver or driver open one; p.40 compensates the **sender**. Collapsing the
  two is how a receiver gets paid for a parcel they never paid for.
* **SEC-07 is enforced by absence, at four levels.** `DriverIncident` has no compensation
  field, `ClaimSummaryForDriver` has no field one could go in, the
  `claims_driver_incidents` table has no such column — proven against PostgreSQL by
  trying the `UPDATE` and getting `UndefinedColumn` — and `may_see_a_compensation_value`
  excludes a driver *first*, so a driver who is also support is still a driver.
* **CLM-06 is a refusal, not a missing route.** `POST /claims/returns/{tracking_code}`
  answers 409 and the reason names the recourse that remains: a claim.
* **Operations was not widened.** Support reviews and talks to the claimant; only
  Operations or an accountant approves money, and only Operations resolves an incident.

`tests/new_service_migration_proof/test_claims_migration.py` attacks each database
constraint for real — approving without a recorded custody review, storing an amount on
an undecided claim, a second open claim on one parcel, a claim about a parcel taken
inside to test, a resolution with a blank note — and 24 concurrent threads allocating
references to prove no two filers get the same one.

## Next, in order

1. `09-FINAL-EXECUTION-REPORT.md` — the final counts and evidence.
2. The four remaining `PLANNED` requirements: **SEC-05** (`identity`), **SHP-04** and
   **SHP-09** (`shipment`), **OPS-03** (`tracking`).
3. `identity` and `customer` are the only two services whose migrations have never been
   proven against PostgreSQL 16. Every other service has a proof in
   `tests/new_service_migration_proof/`.
4. Claims owns an outbox and an inbox table (ADR-0008) that nothing writes to yet. The
   one fact it will carry is "a claim was approved, pay the sender" — and **how** HUDHUD
   pays a compensated sender (wallet credit, payout, cash at a hub) is not stated in
   v6.3. Decide that before wiring it, rather than picking an instrument here.

## Still genuinely unresolved

Five v6.3 executive open items (Appendix A p.44) and two source contradictions — listed
with their evidence in `10-PLATFORM-BOUNDED-CONTEXT-MAP.md` §4 and enforced as fixed sets
by the accounting verifier, so neither list can grow by accident.

| ID | What is refused | What works around it |
|---|---|---|
| `MER-02` | Submitting a merchant application | The whole application, store and policy model |
| `SHP-02` | An exact delivery goal shown to the customer | Tracking, statuses, the public view |
| `PAY-07` | `POST /finance/payouts/{id}/pay` | Request, method, destination, balance check, approval, rejection, both facts |
| `PAY-08` | Waiving the return-trip fee | The charge itself — v6.3 p.38/p.39 settle it — posted to the ledger |
| `CLM-08` | Asking whether a parcel is high value | Everything else in `claims` |
| `DRV-L05` | Verifying a delivery code | The whole doorstep; both readings (4 and 6) recorded and configurable |
| `DRV-L07` | Retaining an ID photograph | The ID verification; **no column exists** to retain one in |

## The Docker labs run concurrently — four causes, all fixed

They used to contend, and the whole root suite produced failures that looked like flaky
eventing. Four separate causes:

1. **Shared names.** Every lab resource is namespaced per process by
   `tests/lab_namespace.py`, interpolated as `${HUDHUD_LAB_SUFFIX:-}`. Pin
   `HUDHUD_LAB_SUFFIX` to share a lab on purpose; set it to `""` for the old fixed names.
2. **A container writing into the host venv.** `eventing-foundation.compose.yaml` ran
   `uv run` without `UV_PROJECT_ENVIRONMENT`, replacing macOS wheels with Linux ones.
3. **`nkeys` installed ad hoc.** `uv pip install nkeys` was removed as extraneous by the
   next `uv run`. It is a declared dev dependency now.
4. **A published port read once, mid-restart.** All four labs took the last line of
   `docker compose port` and split it on a colon. That command answers successfully while
   a container that has just been restarted has no published port yet, so the NATS
   security proof — the only suite that restarts anything — failed intermittently with
   `nats published on unexpected host: ''`. They now share
   `tests/lab_ports.published_binding`, which **polls**; the loopback check is unchanged
   and still fails immediately, because a service on `0.0.0.0` is a finding, not a race.

`tests/architecture/test_lab_isolation.py` (16 tests) guards all four.

```
uv run pytest tests -q     # one session, all four labs
```

## The shared unit-of-work P0

Repaired across all seven affected services and written up in
`13-SHARED-UNIT-OF-WORK-P0.md`. Two guards keep it repaired:

* `tests/architecture/test_request_scoped_state.py` — an AST taint-follower that fails if
  a mutable unit of work or session is ever stored on `app.state` again. `claims` is
  covered by it;
* `tests/new_service_migration_proof/test_request_isolation.py` — 60 tests proving the
  seven isolation properties on real PostgreSQL for finance, delivery, hub, workforce,
  notification and claims.

**`identity`, `customer`, `merchant` and `ordering` are marked `PENDING_CONCURRENT_REPAIR`
and xfail.** They were reported as already repaired by concurrent Flutter integration
work; that repair is **not in this tree**. Do not edit them blind — reconcile with that
work first, then delete them from `PENDING_CONCURRENT_REPAIR`.

## Validation

```bash
uv run --project services/<name> pytest services/<name>/tests
uv run ruff check .
uv run python scripts/quality/verify_boundaries.py
uv run python scripts/quality/verify_agent_governance.py
uv run python scripts/quality/verify_requirement_accounting.py
uv run python scripts/quality/verify_requirement_traceability.py
uv run python scripts/quality/verify_port_allocations.py
uv run pytest tests -q -m "not integration"
uv run pytest tests/new_service_migration_proof -m integration   # needs Docker
```
