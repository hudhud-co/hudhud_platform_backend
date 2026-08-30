---
name: integration-gate
description: >-
  Integrate completed HUDHUD parallel worktrees in dependency order with
  identity, ownership, architecture, test, and migration proofs. Use after a
  parallel-worktree-wave. Does not push unless the current human instruction
  explicitly authorizes it.
disable-model-invocation: true
---

# Integration gate

## Purpose

Merge (or otherwise integrate, as the current task scopes) completed
independent work into the integration branch, proving a clean result SHA.

## When to use

- Parallel tasks from `parallel-worktree-wave` report completion markers
- The human instruction names this skill and names the integrate-to branch

## When not to use

- Any worktree is dirty or off its expected SHA
- File ownership drifted outside the wave matrix
- Using integration to sneak unrelated refactors
- Pushing by default

## Required inputs

- Integration target branch and expected baseline SHA
- Wave matrix (task, branch, SHA, allowed paths)
- Dependency order
- Explicit statement whether merge/rebase is in scope (must be yes for
  this skill) and whether push is authorized (default **no**)

## Preconditions

1. Read `.cursor/rules/03-git-worktree-safety.mdc` and
   `.cursor/rules/08-testing-and-evidence-gates.mdc`.
2. Every contributing worktree: `git status --porcelain` empty,
   HEAD equals the claimed SHA, branch name matches the matrix.
3. Path diffs stay inside `allowed_paths`.
4. No conflict markers in the tree.

## Procedure

1. Verify identity (branch, SHA) for each contributor and the target.
2. Integrate in dependency order using only the Git operations the current
   human instruction scoped (merge is in-scope for this skill; rebase only
   if that instruction said rebase).
3. After each integrate: no conflict markers; `git status` clean.
4. Run architecture, unit/integration/contract tests that exist for touched
   areas, migration `heads` for touched services, disposable upgrade if
   migrations landed, Compose validation if compose landed.
5. Final clean tree. Record integrated SHA.
6. Push **only** if the current human instruction explicitly authorizes push.

## Allowed files or ownership scope

- Integration branch in `hudhud_platform_backend`
- No files outside the union of wave `allowed_paths` plus conflict
  resolution in those same paths
- No legacy repository operations

## Required validation

- Branch and commit identity match the matrix
- Clean worktrees before and after
- Expected file ownership; no unrelated files
- No conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
- `uv run python scripts/quality/verify_boundaries.py`
- `uv run pytest` for `tests/architecture` and any landed service tests
- Migration heads = 1 per touched service
- Disposable upgrade proof if migrations applied
- Compose validation if compose files changed
- Final `git status --porcelain` empty
- Exact integrated SHA recorded
- Push performed: no (unless authorized)

## Stop conditions

- Dirty tree, SHA mismatch, overlap, or unrelated files
- Conflict that requires policy invention
- Test or architecture failure
- Request to push without explicit authorization

## Prohibited actions

- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository
- Dropping user changes to "make the tree clean"
- Integrating work that failed its own skill completion marker

## Output contract

```text
Target branch:
Integrated SHA:
Contributors (branch@sha):
Ownership check:
Conflict markers: none
Architecture tests:
Service tests:
Migration heads:
Compose:
Final status: clean
Pushed: no | yes (authorized)
```

## Completion marker

`HUDHUD_INTEGRATION_GATE_COMPLETE`
