# Development and releases

`developer` is the only development branch.
A release merges `developer` into `main`, then tags the verified merge.
Remove retired remote branches only after their tips are included in `main`.
Their commits remain in the release history.
Do not create another branch for a fix or release.

## Releasing

`nexgen release ship` runs a whole release from a development checkout.
Run it on `developer` with a clean working tree:

```sh
nexgen release ship                                  # next patch version
nexgen release ship minor --notes "One line for the changelog reader"
nexgen release ship 2.5.0 --install                  # explicit version, and update this machine
```

The bump argument is `patch`, `minor`, `major` or an explicit `X.Y.Z`.
Options: `--notes` adds a line on top of the `## [Unreleased]` section of `CHANGELOG.md`, which is where the release notes come from.
`--install` also updates the machine that runs the command.
`--repo PATH` selects the checkout (default: the git repository of the current directory).
`--poll SECONDS` sets the wait between checks (default 30).
The command refuses to run from the installed engine, only from a development checkout.

It prints one `== step` line per step and ends with either `OK vX.Y.Z` or `STOP <step>: <reason>`:

1. **prepare**: checks the branch and the clean tree, fast-forwards from `origin`, bumps `VERSION`, the package `__version__` and `CHANGELOG.md`, runs the preflight, commits `Release vX.Y.Z` and pushes `developer`.
   If the preflight fails, the bump is undone.
   The bump is skipped when `VERSION` is already ahead of the newest tag.
2. **pull request**: opens the `developer` to `main` pull request, or reuses the open one.
3. **checks on the pull request**: waits for the required checks. The `lane-guard` check is ignored here, because it rejects every push that does not come from a feature lane.
4. **merge**: merges with a merge commit.
5. **CI on main**: waits for `ci.yml` on the merge commit.
6. **tag**: creates the tag with `git tag -s`, signed by the key this machine's git is configured to use.
   It then verifies the tag against the trust anchor of the installed copy (`trust/release-signers.txt`).
   If that copy would refuse the tag, the tag is deleted locally and nothing is pushed.
7. **release workflow**: pushes the tag, waits for `release.yml`, and reruns it once if it fails.
   It then checks that the GitHub release is not a draft and has assets.
8. **sync developer**: waits for `sync-developer` to merge `main` back into `developer`.
9. **install** (only with `--install`): runs `nexgen update --yes --target vX.Y.Z`.
   If the update fails and is rolled back, it retries once.
   It then checks that the installed `VERSION` matches and runs `nexgen doctor --summary`, which stops it only on failures (warnings are non-blocking).

Every maintainer machine signs with its own key, and that key has to be listed in `trust/release-signers.txt`.
Installed copies only accept signers they pin, so a new key takes two releases (see `SECURITY.md`).

After a stop, fix the reason and run it again; it resumes:
a version that is already bumped is not bumped again, an open release pull request is reused,
and a release commit already on `main` skips straight to the CI and the tag.
After the tag is pushed there is nothing left to redo: a rerun while `main` is not yet back in `developer`
stops with "still finishing" instead of bumping again. Wait for `sync-developer`, then run `nexgen update`.

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
