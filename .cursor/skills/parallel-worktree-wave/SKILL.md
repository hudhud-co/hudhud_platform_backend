---
name: parallel-worktree-wave
description: >-
  Prepare a controlled HUDHUD parallel development wave with one branch/worktree
  per bounded task, a path-ownership matrix, and no overlapping files. Use before
  parallel Cursor windows. Does not start implementation and does not push.
disable-model-invocation: true
---

# Parallel worktree wave

## Purpose

Create isolated git worktrees for independent HUDHUD tasks from a **clean**
approved baseline, with a matrix that prevents overlapping file ownership.

## When to use

- The human instruction names this skill or asks to prepare parallel worktrees
- Multiple bounded tasks are ready **after** ADRs/ownership are decided

## When not to use

- Starting feature implementation inside this skill
- Tasks that share files, a migration head, or an event contract owner
- Unclean baseline, unexpected HEAD, or uncommitted user work on the baseline
- Creating worktrees on the legacy repository

## Required inputs

- Approved baseline branch and SHA
- List of bounded tasks (name, owning context, intended paths)
- Dependency order (who must integrate first)

## Preconditions

1. Baseline worktree: expected branch, expected HEAD, `git status --porcelain`
   empty.
2. Each task has an accepted ADR or an explicit invariant already covering it.
3. Path sets are written down **before** `git worktree add`.
4. Read `.cursor/rules/03-git-worktree-safety.mdc`.

## Procedure

1. Map dependencies (events, contracts, migrations, packages).
2. If any two tasks write the same path, **stop**. They are not independent.
3. For each task: create one branch from the baseline SHA and one worktree
   with a dedicated directory **outside** the legacy repo.
4. Record the matrix (task, branch, worktree path, allowed paths, depends-on).
5. Do not run service generators, tests that mutate, or implementation edits.
6. Do not push branches.

## Allowed files or ownership scope

- Git worktree/branch creation on `hudhud_platform_backend` only
- Optional wave notes if the current task explicitly names a path under
  `docs/` (otherwise report in the skill output only)
- No application/service source edits

## Required validation

- Baseline still clean.
- `git worktree list` shows one worktree per task.
- Path sets are disjoint (including Docker, Alembic, contracts, and tests).
- No worktree was created under the legacy absolute path.

## Stop conditions

- Baseline dirty or HEAD mismatch
- Overlapping file ownership or shared migration history
- Hidden coupling (same contract file, same package, same Compose service)
- Any task still `undecided` on writer/database strategy

## Prohibited actions

- Starting implementation or scaffolding services
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository
- Merging the wave in this skill (that is `integration-gate`)

## Output contract

```text
Baseline branch:
Baseline SHA:
Matrix:
  - task:
    branch:
    worktree:
    allowed_paths:
    depends_on:
Independence: confirmed | failed
Implementation started: no
Pushed: no
```

## Completion marker

`HUDHUD_PARALLEL_WORKTREE_WAVE_READY`
