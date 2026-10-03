# Agent lanes — the single branch contract for AI agents

You are an AI agent working in this repo. There is exactly one workflow.
No forks, no per-fix branches, no direct commits to integration or release.

## The map (4 rungs, no more)

- `main` — history. Never commit here, never merge here.
- `developer` — integration. Everything lands here first.
- `dev/<agent>` — durable lanes, one per agent (`dev/engine`, `dev/council`).
  A lane lives as long as the agent's area does. Never delete another
  agent's lane, never commit on it.
- `release/*` — releases only. Advances from `developer` at version time,
  by merge. Nothing else ever lands here.

## Rules

1. Work on your `dev/<agent>` lane. If it does not exist, cut it from
   `developer` (`git checkout -b dev/<agent> developer`).
2. Commit on your lane whenever tests are green. Push the lane when useful.
3. Merge lane → `developer` at end of session, only with the lane's test
   gate green. One merge per session, not per fix.
4. `developer` → `release/*` happens at version bumps only.
5. NEVER commit directly on `developer`, `main`, or `release/*` — not even
   "a trivial fix". Trivial fixes ride your lane like everything else.

## Forbidden (checked by CI and by `nexgen doctor`)

- Non-merge commits on `release/*` that are not `release:` chores.
- A `release/*` tip that does not descend from `developer`.
- Uncommitted work sitting on `main`/`release/*` (move it to your lane).

Before integrating, run `python 03-INFRA/scripts/check_engine.py`.
Module ownership and regression expectations: `CONTRIBUTING.md`.

Use a separate Git worktree when another session shares this checkout.
A branch alone does not isolate files from another writer.

Full rationale: `docs/agent-lanes.md`.
