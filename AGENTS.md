# One development branch

Work on `developer`. Publish releases by merging `developer` into `main`.
Do not create `dev/*`, `feat/*`, `fix/*`, `draft/*`, or `release/*` branches.
Existing branches preserve history; they are retired development lanes.
Do not delete another session's branch or rewrite published history.

Commit signed changes on `developer` after the relevant tests pass.
Run `python 03-INFRA/scripts/check_engine.py` before pushing or releasing.
Keep one commit per topic and preserve unrelated work.
Never commit development directly on `main`.

Use an isolated worktree when another session shares the checkout.
A detached worktree may hold uncommitted review work; one integrator applies
and commits the verified result on `developer`.
A branch in a shared directory does not isolate files from another writer.

The `sync-developer` workflow merges `main` back into `developer` whenever
`main` advances. Conflicts fail the workflow; neither branch is force-pushed.
Keep automatic branch deletion disabled so a release preserves `developer`.

Read module owners, callers and tests in `CONTRIBUTING.md` before editing.
Shared behavior has one owner; drivers import it. Preserve existing entry
points and verify resumed state when changing its data contract.
Authorized maintainer work does not require an external-contributor issue.

Details and enforcement: `docs/agent-lanes.md`.
