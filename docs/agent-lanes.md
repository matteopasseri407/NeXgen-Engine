# Agent lanes: one workflow for every AI agent

`AGENTS.md` (repo root) is the contract. This note is the why, the exact
gate behavior, and what to do when a gate fires.

## Why lanes, not branches-per-fix

Two agents on one tree stepped on each other; direct commits on release
branches bypassed CI-after-the-fact; prose-coupled tests turned every fix
into 60 breakages. The fix is structural, not disciplinary:

- durable `dev/<agent>` lanes (no 400 throwaway branches),
- one integration point (`developer`),
- CI checks integration history and doctor reports misplaced work.

## The gates

### CI: `lane-guard`

Runs on pushes and pull requests. PRs check the target branch against its
base commit using the proposed merge snapshot. Pushes use the previous tip.

- `main` is frozen: advancing it fails the check.
- `developer` must advance through merges; direct first-parent commits fail.
- `release/*` must descend from `developer`, and additional non-merge
  commits must have a release-chore subject (`release:`, `Release v`, or
  `chore(release)`). Remote-tracking refs work in fresh CI clones.
- A `dev/*` lane is allowed to contain ordinary development commits.

A missing required ref fails the check. The implementation lives in
`nexgen_core/lanes.py`; `03-INFRA/scripts/lane_guard.py` is its entry point.
For a local integration check, pass the previous tip with `--base`.
CI reports a failed job; branch protection determines whether GitHub blocks
merging. It does not reject a Git push that has already reached the server.

### Doctor: engine-lane watch

`nexgen doctor` reuses the lane check. Dirty work on guarded branches produces
a warning; missing refs produce an undetermined result. It never rewrites
branches or moves files. Run doctor explicitly; the normal guard cycle does
not schedule this contributor check.

## Merge rhythm (lean, no PR bureaucracy)

- During a session: commit freely on your lane (green tests).
- End of session: `git checkout developer && git pull --ff-only &&
  git merge --no-ff dev/<agent> -m "merge dev/<agent>: <what>"`.
- Version time (maintainer): `developer` → `release/x.y.z`.
- Conflicts on the lane merge: resolve on the lane, re-run the lane gate,
  merge again. Never `--force` shared rungs (`developer`, `release/*`,
  another agent's lane).

## When CI is red on your merge

Do the repair on your lane. If a merge must be reverted, make the revert on
the lane, test it, then merge the lane into `developer`. Never repair red CI
with a direct integration-branch commit.
