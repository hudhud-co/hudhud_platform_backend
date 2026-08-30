# HUDHUD Platform Agent Instructions

This repository is `hudhud_platform_backend`: a production-grade monorepo for independently
deployable FastAPI services. The git repository is a **development boundary**, not a runtime
boundary. Each service deploys, scales, fails, and migrates independently.

Foundation work is governance, architecture, and evidence first. Do not invent missing
business policy or treat a suggested deployable count as an architectural fact.

## Architecture authority

Highest authority wins. A Skill may define workflow steps but **must never** override
architectural or safety rules.

1. Explicit human instruction in the current task
2. This file (`AGENTS.md`)
3. Approved ADRs under `docs/adr/`
4. `architecture/invariants.md`
5. `architecture/service-boundaries.yaml`
6. Scoped Cursor Rules under `.cursor/rules/`
7. Invoked Cursor Skill workflow under `.cursor/skills/`
8. Existing implementation conventions in this repository

Cite canonical documents. Do not duplicate or fork them inside rules, skills, or reports.

Related ownership data: `architecture/ownership-matrix.yaml`.

## Service and data ownership

- Every bounded context has one canonical writer. See `architecture/ownership-matrix.yaml`.
- A genuine service owns its runtime, dependencies, lockfile, migrations, database
  credentials, contracts, tests, deployment, observability, and recovery.
- Gateway routes, authenticates, and forwards. It owns no business tables and no
  business orchestration.
- Transitional deployable grouping does not erase bounded-context ownership.

## Hard boundaries

- **No cross-service Python imports.**
- **No shared ORM and no shared domain model.** `packages/` may contain only allowlisted
  technical primitives declared in `architecture/service-boundaries.yaml`.
- **No cross-service database access**, credentials, or foreign keys. Reference by ID
  and communicate through versioned HTTP/events.
- **No direct dependency on the legacy repository.** `hudhud-backend` is never a runtime,
  path, submodule, or Docker build dependency.

## Legacy read-only policy

Absolute path (read-only evidence source):

`/Users/mohammadakbari/Development/Projects/Python/hudhud-backend`

- Inspect only. Never edit, format, stage, stash, restore, reset, commit, generate files,
  add submodules/symlinks, or add path dependencies.
- Do not copy blindly. Intentional behavioral ports require a provenance record in
  `docs/audit/legacy-provenance.yaml`.
- Preserve the pre-existing unstaged user file
  `scripts/dev_pickup_driver_simulator.py` in the legacy worktree. Do not touch it.
- Configuration **names** may be inspected. Secret **values** must not be printed.

## Shipment, messaging, and cutover

- **Shipment** is the sole canonical writer of shipment lifecycle state.
  Pickup, Hub, Linehaul, and Delivery publish facts or issue commands; they do not
  mutate canonical Shipment state.
- Cross-service messaging is **at-least-once** (NATS JetStream). Producers use a
  transactional outbox. Consumers use a durable idempotent inbox. Do not claim
  exactly-once delivery.
- Database extraction uses **one-writer cutover**. Bidirectional dual-write is forbidden.
  Credential revocation is a mandatory cutover gate.

## Evidence-based engineering

- Record acceptance criteria before implementation.
- Distinguish evidence, proposal, decision, assumption, and unresolved policy.
- Quote exact commands and results. Do not claim "production-ready" without evidence.
- Consult `docs/audit/` for legacy inventories. Do not treat dirty legacy files as
  platform changes.

## Scope discipline

- One bounded task per branch/worktree.
- Change only files in the assigned ownership scope.
- Do not implement adjacent services, infrastructure, or policy as a side effect.
- List deferred scope and non-actions explicitly.

## User-change preservation

User changes are never discarded or overwritten. Unrelated dirty or untracked files
must be left untouched. Do not stash, restore, reset, or reformat them.

## Secret handling

- No secrets in source, prompts, logs, fixtures, or reports.
- Never commit `.env*` values.
- Database credentials are scoped per service.
- Service-to-service identity must be explicit. Do not trust arbitrary forwarded
  identity headers.
- Configuration audits list names only.
- No real customer data in tests or generated artifacts.

## Git and worktree safety

- Capture branch, HEAD, and `git status` before edits.
- No destructive Git (`reset --hard`, `clean -fd`, force checkout, history rewrite).
- No merge, rebase, or cherry-pick unless the current task explicitly scopes it.
- No worktree creation unless the current task (or an invoked skill) explicitly
  requires it.
- Final verification must show a clean task-owned tree and exact commit/diff evidence.

## Test and validation requirements

When the touched area has a gate, run it and report exact output:

```bash
uv run ruff check .
uv run python scripts/quality/verify_boundaries.py
uv run python scripts/quality/verify_agent_governance.py
uv run pytest tests/architecture tests/governance
```

Add focused tests for behavior changes plus required regression coverage. Do not
weaken or delete existing architecture tests.

## Commit and push policy

- Local commit only when the current human instruction requests it.
- **No push** and **no pull request** unless the current human instruction
  explicitly authorizes that action.
- Do not skip hooks.

## Stop conditions

Stop without mutation (or without further mutation) when:

- The worktree is not the expected branch/HEAD, or unexpected dirty files would be
  overwritten.
- Ownership, writer, or database strategy is undecided and the task needs a decision.
- A request would mutate the legacy repository or discard user changes.
- Workstreams overlap files or are not actually independent.
- An invariant in `architecture/invariants.md` would be violated.
- Required evidence, tests, or recovery/rollback plan is missing.
- Unresolved policy would be silently treated as an approved decision.
- Push, production access, or destructive Git is requested without explicit
  authorization and a recovery plan.
