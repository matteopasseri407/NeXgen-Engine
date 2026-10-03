# Agent lanes: one workflow for every AI agent

`AGENTS.md` (repo root) is the contract. This note is the why, the exact
gate behavior, and what to do when a gate fires.

## Why lanes, not branches-per-fix

Two agents on one tree stepped on each other; direct commits on release
branches bypassed CI-after-the-fact; prose-coupled tests turned every fix
into 60 breakages. The fix is structural, not disciplinary:

- durable `dev/<agent>` lanes (no 400 throwaway branches),
- one integration point (`developer`),
- machines enforce it (CI blocks, doctor warns) so nobody has to remember.

## The gates

### CI: `lane-guard` (blocking)

Runs on every push to `main` and `release/*`. It fails the push when:

1. the tip does not descend from `developer`
   (`git merge-base --is-ancestor developer <ref>`), or
2. any non-merge commit in `developer..<ref>` is not a `release:` chore
   (`^(release:|Release v|chore\(release\))`).

A red `lane-guard` means: move the commits to your lane and merge
lane → `developer` instead. The script is `03-INFRA/scripts/lane_guard.py`
(stdlib only); the same check runs anywhere with
`python3 03-INFRA/scripts/lane_guard.py --ref <branch>`.

### Doctor: engine-lane watch (warning, hourly via guard)

`nexgen doctor` reports WARN when the engine checkout sits on
`main`/`release/*` with uncommitted changes or commits ahead of
`developer`: that work belongs on a `dev/<agent>` lane. Warnings never
block; they expire the moment the work moves.

## Merge rhythm (lean, no PR bureaucracy)

- During a session: commit freely on your lane (green tests).
- End of session: `git checkout developer && git pull --ff-only &&
  git merge --no-ff dev/<agent> -m "merge dev/<agent>: <what>"`.
- Version time (maintainer): `developer` → `release/x.y.z`.
- Conflicts on the lane merge: resolve on the lane, re-run the lane gate,
  merge again. Never `--force` shared rungs (`developer`, `release/*`,
  another agent's lane).

## When CI is red on your merge

Red on `developer` blocks everyone downstream. Revert the merge
(`git revert -m 1 <merge>` on `developer`), fix on your lane, merge again.
A reverted merge is routine, not a failure.
