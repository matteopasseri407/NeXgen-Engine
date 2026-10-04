# Development and releases

`developer` is the only development branch.
A release merges `developer` into `main`, then tags the verified merge.
Existing `dev/*` and `release/*` branches preserve history and are retired.
Do not create another branch for a fix or release.

## Verification and concurrent work

Work and commit on `developer`; keep commits signed and run the nearest
regression tests before committing.
Before pushing or releasing, run `python 03-INFRA/scripts/check_engine.py`.
Public publication also requires the leak scan, a signing identity verified
by GitHub, and all required CI checks.

Concurrent sessions use isolated worktrees.
A detached worktree can hold review changes until one integrator applies
and commits them on `developer`; it must not publish a new branch.
Preserve another session's dirty files and never rewrite published history.

## CI and doctor

`nexgen_core/lanes.py` owns the branch checks used by CI and doctor.
The `lane-guard` job checks push ranges and proposed PR merge snapshots:

- `developer` accepts development commits and must include published `main`.
- `main` accepts merges from `developer` and refuses direct development commits.
- Other branch names fail the development contract.
- A missing previous tip or required ref fails closed.
  A first push requires an explicit published base.

CI reports violations after a push; GitHub branch protection determines
which failed checks block a release merge.
Doctor accepts local work on `developer`, warns on work on `main` or retired
branches, and reports missing remote refs as unverified.
It never moves or commits that work.

## Automatic alignment

`.github/workflows/sync-developer.yml` runs after `main` advances and can also
be started manually.
Its Python entry point requests an atomic GitHub merge of `main` into
`developer`; an already aligned branch is a successful no-op.
Conflicts, missing branches and transport failures stop the workflow.
Resolve a conflict on `developer`, verify the result, and push that branch.
The workflow creates no temporary branch and never force-pushes.

Automatic deletion of merged branches must remain disabled in the repository
settings, so merging a release keeps `developer` available.
Local checkouts receive the resulting remote state through a normal fetch
and fast-forward pull; the workflow never touches local worktrees.
