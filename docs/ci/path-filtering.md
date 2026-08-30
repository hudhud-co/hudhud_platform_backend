# CI path filtering

Architecture-aware path impact calculation for GitHub Actions. The logic lives in
`scripts/ci/path_impact.py` and is invoked by `.github/workflows/ci.yml`.

## Comparison range

| Event | Base SHA | Head SHA |
|-------|----------|----------|
| `pull_request` | `github.event.pull_request.base.sha` | `github.event.pull_request.head.sha` |
| `push` | `github.event.before` | `github.sha` |

When the base SHA is missing, all zeros (new branch), or unavailable in a shallow clone,
calculation **fails safe** to full validation.

## Classification

Changed paths are classified into:

- **service** — `services/<name>/…`
- **package** — `packages/<name>/…` (shared technical primitives)
- **contracts** — `contracts/…`
- **architecture** — `architecture/…`
- **governance** — `.cursor/…`, `AGENTS.md`, `docs/adr/…`, `tests/architecture/…`, `tests/governance/…`
- **infrastructure** — `infra/…`
- **ci_tooling** — `.github/…`, `scripts/…`, root lock/tooling files
- **docs_only** — `docs/…` markdown (excluding ADRs) and root `README.md`
- **unknown** — unmapped executable or configuration paths

Renames and deletions classify **both** old and new paths so validation cannot be bypassed.

## Fail-safe triggers

Full validation runs when any of the following is true:

- Unknown or unmapped executable/configuration path
- Missing or unavailable base/head SHA
- Shared package, contract, architecture, governance, infrastructure, or CI/tooling change
- Root dependency lock change (`pyproject.toml`, `uv.lock`)

## Required gates (never skipped)

These run on every workflow regardless of path filter outcome:

- `uv lock --check`
- `uv run ruff check .`
- `uv run python scripts/quality/verify_boundaries.py`
- `uv run python scripts/quality/verify_agent_governance.py`
- `uv run pytest tests/architecture tests/governance tests/ci`

Docs-only changes may skip service-scoped jobs but **never** skip architecture or governance gates.

## Local usage

```bash
# Synthetic diff lines (git name-status format)
uv run python scripts/ci/calculate_path_impact.py \
  --changed-file 'M	services/shipment/src/shipment/main.py'

# Git range
uv run python scripts/ci/calculate_path_impact.py \
  --base "$(git merge-base HEAD develop)" \
  --head HEAD

# GitHub Actions output format
uv run python scripts/ci/calculate_path_impact.py \
  --format github \
  --changed-file 'M	docs/audit/legacy-baseline.md'
```

## Output

Machine-readable JSON (`version: 1`) with sorted lists for deterministic CI caching.
GitHub Actions job outputs are derived from the same payload via `--format github`.

## Extensibility

As services and packages are scaffolded under `services/` and `packages/`, they are
picked up automatically by path prefix rules. No workflow edit is required per service
until optional service-scoped jobs need additional commands.
