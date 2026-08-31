# CDC staging drill command tiers

Three explicit tiers. **Default for all scripts: dry-run** (`CDC_DRILL_DRY_RUN=1`).

| Tier | Script | Purpose |
|------|--------|---------|
| Read-only | `readonly.sh` | Preflight SQL, slot inspect, lag read, manifest validate |
| Privileged/manual | `privileged-manual.sh` | Replication-protocol templates — operator executes via client |
| Destructive cleanup | `destructive-cleanup.sh` | Slot drop — requires confirmation + exact slot name |

## Safety rules

1. Source `config.example.env` or local git-ignored config — never commit secrets.
2. Preflight **FAIL** → do not run privileged or destructive tiers.
3. Destructive tier never runs when `CDC_DRILL_CONFIRM_DESTRUCTIVE=0` (default).
4. No script performs `ALTER SYSTEM`, server restart, or broad slot cleanup.

## Environment variables

See `../config.example.env` for full list. Critical guards:

- `CDC_DRILL_DRY_RUN` — `1` print only (default), `0` execute
- `CDC_DRILL_CONFIRM_DESTRUCTIVE` — must be `1` for slot drop
- `CDC_DRILL_SLOT_NAME` — exact slot name for destructive ops

## Replication-protocol note

Privileged slot creation **cannot** be faked with ordinary SQL. Use `pg_recvlogical`, Debezium,
or libpq replication API. Templates in `privileged-manual.sh` are commented references only.
