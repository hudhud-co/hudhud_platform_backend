# CDC Staging Zero-Gap Drill Runbook (ADR-0007)

**Artifact type:** Kit — reusable procedure template.  
**This document is not executed staging evidence.**  
**Related:** ADR-0007 gates G1–G10; ADR-0006 stage 7 (zero-gap completeness).

---

## Purpose

Validate on **staging** (future execution):

1. Logical replication prerequisites (G1)
2. Least-privilege replication credentials (G2)
3. Slot/WAL safety and monitoring (G3)
4. **Coordinated** snapshot + logical slot creation (G4)
5. Durable Bridge landing before slot feedback (G5)
6. Restart/resume behavior
7. Zero-gap reconciliation (G8)
8. Cleanup and slot retirement evidence

**This kit does not connect to staging during repository work.** Operators run it manually
when authorized.

---

## Snapshot and slot protocol (read before any privileged step)

| Mechanism | What it proves | Zero-gap eligible? |
|-----------|----------------|--------------------|
| **Ordinary SQL snapshot** | `BEGIN ISOLATION LEVEL REPEATABLE READ` + `pg_export_snapshot()` returns a snapshot id for **this transaction** | **No alone** — not atomically bound to a logical slot `restart_lsn` |
| **Current WAL LSN read** | `pg_current_wal_lsn()` at query instant | **No** — point-in-time marker only |
| **Replication-protocol exported snapshot** | `CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT` (or equivalent client API) returns **both** `snapshot_id` **and** slot `restart_lsn` in one protocol step | **Required for HWM** — ADR-0007 G4 |
| **Illustrative count/HWM test** | Row `COUNT(*)` or lab helper returning count + LSN in one SQL function | **No** — transport illustration only (W3-C lab scenario 10) |

**Binding rule:** Do **not** claim zero-gap proof unless the drill genuinely coordinates
backfill snapshot identity with logical slot `restart_lsn` via the **replication protocol**,
not ordinary SQL alone.

**Required tooling:** A replication-protocol client is **mandatory** for G4. Acceptable
examples (operator choice — not prescribed by this kit):

- `pg_recvlogical` with `CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT`
- Debezium / logical replication connector create-slot API
- Application code using libpq replication connection

**Forbidden substitute:** Running preflight SQL counts or lab `capture_hwm_snapshot()` helpers
and treating the result as production zero-gap evidence.

---

## Drill phases and gates

| Phase | Name | Tier | ADR gate | Proceed when |
|-------|------|------|----------|--------------|
| 0 | Kit load + config validate | Read-only | — | `validate.py` passes; dry-run default confirmed |
| 1 | Preflight SQL | Read-only | G1, G2, G7 | All **FAIL** rows resolved or waived with evidence |
| 2 | Allowlist inventory | Read-only | G7 | `allowlist-inventory.yaml` completed and signed |
| 3 | Coordinated HWM (slot + snapshot) | Privileged/manual | **G4** | Replication-protocol slot created; snapshot id + `restart_lsn` recorded |
| 4 | Backfill from exported snapshot | Privileged/manual | G5 prep | Repeatable-read export ≤ HWM; counts/checksums captured |
| 5 | Live CDC + durable landing | Privileged/manual | **G5** | Bridge lands row before slot feedback; no early `get_changes` ack |
| 6 | Synthetic post-HWM writes | Privileged/manual | G8 | Controlled writes after HWM; appear in landing or DLQ |
| 7 | Restart / resume | Privileged/manual | G8 | Stop consumer; WAL grows within thresholds; resume without gap |
| 8 | Reconciliation | Read-only | G8 / ADR-0006 §7 | Checklist pass; duplicate `event_id` = duplicate_safe only |
| 9 | Cleanup + evidence pack | Destructive (optional) | G3, G9 | Slot retired with evidence; manifest complete |

**Preflight failure stops the drill** — do not proceed to phase 3.

---

## Phase 0 — Kit load

1. Copy `config.example.env` to a **git-ignored** local file.
2. Set identity fields (`CDC_DRILL_ID`, operator, environment).
3. Confirm `CDC_DRILL_DRY_RUN=1` until deliberate execution.
4. Run validator:

```bash
uv run python infra/cdc/staging-drill/validate.py \
  --config /path/to/cdc-drill.local.env \
  --manifest infra/cdc/staging-drill/templates/evidence-manifest.yaml
```

---

## Phase 1 — Read-only preflight

```bash
# Default: dry-run prints psql invocation
sh infra/cdc/staging-drill/commands/readonly.sh preflight
```

Execute `sql/preflight-readonly.sql` with the **read-only** role. Capture full output into
evidence directory. Any `status = FAIL` row blocks phase 3 unless waived with written ops approval.

Record separately (OS level):

- WAL volume free space vs `CDC_DRILL_MIN_WAL_VOLUME_FREE_BYTES`
- Existing slot inventory and lag trends

---

## Phase 2 — Allowlist inventory

Complete `templates/allowlist-inventory.yaml`. Minimum allowlist for ADR-0007 accepted
observations:

- `public.shipment_events` → `legacy_bridge.observation.shipment_timeline_entry` v1
- `public.audit_logs` → `legacy_bridge.observation.audit_entry` v1

Confirm PII classification and column minimization per ADR-0002.

---

## Phase 3 — Coordinated HWM (G4)

**Tier:** privileged/manual — requires replication role and change window approval.

Use replication-protocol tooling (not ordinary SQL) to:

1. Create logical slot with `EXPORT_SNAPSHOT`.
2. Record atomically: `slot_name`, `plugin`, `snapshot_id`, `restart_lsn`, timestamp, operator.
3. Verify slot appears in `pg_replication_slots` with expected `restart_lsn`.

See `commands/privileged-manual.sh` for **commented templates** — never auto-executed.

---

## Phase 4 — Backfill

1. Open repeatable-read transaction using exported `snapshot_id`.
2. Export allowlisted tables ≤ HWM boundary.
3. Compute per-table row counts and PK-set checksums (store in manifest).
4. Compute `event_id` per ADR-0007 A1/A2 formula for sample rows.

Backfill and live CDC **must** produce the same `event_id` for the same append-only row.

---

## Phase 5 — Live CDC + durable landing (G5)

**Ack order (binding):**

```text
peek/decode → durable Bridge landing commit → publish → slot advance/feedback
```

Bridge slot/cursor position **may advance only after** durable landing evidence exists for
the decoded change. Never call `pg_logical_slot_get_changes` (or auto-ack) before landing commit.

---

## Phase 6 — Synthetic post-HWM validation

After HWM activation, apply controlled staging writes to allowlisted tables (staging only,
authorized window). Every committed row **R** after HWM must appear in Bridge landing or DLQ
within lag SLO.

---

## Phase 7 — Restart / resume

1. Stop Bridge consumer gracefully (slot remains).
2. Monitor WAL lag against warn/stop thresholds (`checklists/stop-conditions.md`).
3. Restart consumer; verify no gap between pre-stop and post-resume landing.
4. Record restart checkpoints in evidence manifest.

---

## Phase 8 — Reconciliation

Follow `checklists/reconciliation.md`. Semantic reconciliation must prove
`backfill ∪ live CDC` completeness. Row-count equality alone is insufficient (ADR-0006).

---

## Phase 9 — Cleanup and evidence package

1. Complete `templates/evidence-manifest.yaml` with all sections.
2. Run validator on final manifest.
3. If slot retirement required, follow `procedures/cleanup-slot-retirement.md` (destructive tier).

Archive evidence with pass/fail per ADR-0007 gate G1–G10 applicable to this drill.

---

## Command tier separation

| Tier | Script | Default | Auto-executes |
|------|--------|---------|---------------|
| Read-only | `commands/readonly.sh` | Dry-run | Preflight, lag checks, manifest validate |
| Privileged/manual | `commands/privileged-manual.sh` | Dry-run | Slot create templates — **manual** replication client |
| Destructive cleanup | `commands/destructive-cleanup.sh` | **Blocked** | Slot drop only with explicit confirmation + exact name |

**Never:**

- Automatic `ALTER SYSTEM`
- Automatic PostgreSQL restart
- Automatic slot drop
- Recursive/broad cleanup

---

## Evidence package contents (future execution)

The executed drill must record (no secrets in stored artifacts):

| Section | Fields |
|---------|--------|
| Identity | drill id, environment, operator, timestamps |
| Server config | PostgreSQL version, wal_level, slot/sender limits, ssl mode |
| HWM | snapshot id, consistent point, restart_lsn, slot name, plugin |
| Backfill | per-table counts, PK checksums, sample event_id collisions |
| Live CDC | WAL range captured, landing commit timestamps |
| Restart | stop/start times, lag at stop/resume, checkpoint positions |
| Reconciliation | gap/duplicate findings, pass/fail per checklist item |
| WAL growth | lag bytes timeline, warn/stop threshold breaches |
| Cleanup | slot drop evidence, credential revocation notes if applicable |
| Gates | pass/fail for each ADR-0007 gate exercised |

Use `templates/evidence-manifest.yaml` as the canonical structure.

---

## ADR-0007 gate mapping

| Gate | Drill evidence |
|------|----------------|
| G1 | Preflight wal_level, slot/sender capacity |
| G2 | Read-only vs replication role proof |
| G3 | Lag monitoring, stop-condition exercise |
| G4 | EXPORT_SNAPSHOT + slot coordinated record |
| G5 | Landing-before-feedback trace |
| G6 | **Out of kit scope** — HA/failover strategy documented separately |
| G7 | Allowlist inventory + PII minimization |
| G8 | Phase 6–8 zero-gap + restart evidence |
| G9 | Cleanup / credential notes |
| G10 | Observation contract mapping in allowlist |

---

## References

- ADR-0006 stage 7 — post-HWM capture completeness
- ADR-0007 — Bridge strategy, pre-HWM stages A–D
- `infra/labs/legacy-cdc/OPERATIONS.md` — lab limitations (illustrative helpers)
