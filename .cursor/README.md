# HUDHUD Cursor Governance

Repository-controlled engineering assets for Cursor Auto windows working in
`hudhud_platform_backend`. These files are versioned with the code. Changing them
is a governance change, not a personal IDE preference.

Canonical agent instructions: [`../AGENTS.md`](../AGENTS.md).

Architecture authority (do not fork):

- [`../architecture/invariants.md`](../architecture/invariants.md)
- [`../architecture/service-boundaries.yaml`](../architecture/service-boundaries.yaml)
- [`../architecture/ownership-matrix.yaml`](../architecture/ownership-matrix.yaml)

## Rules vs Skills

| Asset | Path | Role |
|-------|------|------|
| **Rules** | `.cursor/rules/*.mdc` | Persistent constraints. Loaded by activation mode. They bind safety, architecture, and evidence. |
| **Skills** | `.cursor/skills/<name>/SKILL.md` | Invoked workflows. They sequence a bounded task. They never override `AGENTS.md`, approved ADRs, or architecture invariants. |

Rules answer "what must always be true." Skills answer "how to carry out this named procedure."

## Always-active rules

Loaded for every Cursor session in this repository:

| File | Concern |
|------|---------|
| `rules/00-repository-authority.mdc` | Identity, authority hierarchy, evidence, scope, stop conditions |
| `rules/01-legacy-read-only.mdc` | Read-only `hudhud-backend` policy |
| `rules/02-architecture-boundaries.mdc` | Service ownership and isolation |
| `rules/03-git-worktree-safety.mdc` | Git, worktrees, commit/push policy |
| `rules/07-security-and-secrets.mdc` | Secrets, identity, least privilege |
| `rules/08-testing-and-evidence-gates.mdc` | Acceptance criteria, tests, integrity |

## Scoped rules

Auto-attached when matching files are in context (`alwaysApply: false` + `globs`):

| File | Typical files |
|------|----------------|
| `rules/04-python-service-quality.mdc` | Python, `pyproject.toml`, `uv.lock`, `services/`, `packages/`, `tests/` |
| `rules/05-database-migrations.mdc` | Alembic, persistence, ORM/migration paths |
| `rules/06-events-and-messaging.mdc` | Contracts, outbox/inbox, NATS, producers/consumers |
| `rules/09-adr-and-documentation.mdc` | `docs/`, `architecture/`, `contracts/` |

Do not convert scoped rules into always-on rules. That wastes context and invites stale duplication.

## How a future prompt invokes a Skill

Name the skill in the human instruction. Skills are stored in-repo and are intended
to be invoked explicitly so parallel windows do not load unrelated workflows.

Examples (HUDHUD terminology only):

```text
Use skill `legacy-evidence-audit` for the Shipment bounded context.
Read the legacy repository at the documented absolute path. Do not modify it.
```

```text
Use skill `prepare-adr` for shipment sole lifecycle writer.
Do not treat unresolved finance policy as an accepted decision.
```

```text
Use skill `parallel-worktree-wave` to prepare independent worktrees for
Pickup fact-publication and Hub fact-publication. Do not start implementation.
Do not push.
```

```text
Use skill `bootstrap-service` to scaffold `services/shipment` after the
ownership ADR is accepted. Scaffold only that service.
```

```text
Use skill `integration-gate` to integrate completed Pickup and Hub worktrees
in dependency order. Do not push unless this instruction explicitly authorizes push.
```

The agent must read `.cursor/skills/<skill-name>/SKILL.md` and follow its procedure,
then emit that skill's completion marker.

## Parallel Cursor windows and worktrees

Each parallel window owns **one** branch and **one** worktree created from a clean
approved baseline. Prepare the wave with `parallel-worktree-wave` before coding.

Why file scopes must not overlap:

- Two windows editing the same path will silently clobber each other.
- Bounded-context ownership and migration history are per service.
- Integration (`integration-gate`) can only prove independence if the wave
  declared disjoint path ownership up front.

The wave output is a worktree/branch/path matrix. A window may edit only the
paths listed for its task.

## Completion markers

Every skill ends by emitting a unique marker (exact string in that skill's
**Completion marker** section), plus the output contract. Report:

- marker
- branch and HEAD
- files changed
- commands and results
- deferred decisions and non-actions

Do not claim a skill complete without its marker and required evidence.

## Changing governance files safely

1. Treat `.cursor/**` and `AGENTS.md` as engineering assets: one bounded change,
   tests updated in the same change.
2. Do not weaken architecture tests or invert a forbidden boundary into an allow.
3. Run:

   ```bash
   uv run python scripts/quality/verify_agent_governance.py
   uv run pytest tests/architecture tests/governance
   ```

4. Commit only when requested. Do not push unless explicitly authorized.

A Skill workflow never outranks `AGENTS.md` or `architecture/invariants.md`.
