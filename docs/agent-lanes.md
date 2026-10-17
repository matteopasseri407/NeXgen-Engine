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
   No pull request is opened when it would change no file: if `main` already carries the branch's tree (what `sync-developer` leaves behind) the release goes straight to the checks and the tag.
3. **smoke here, and checks on the pull request**: this machine's own smoke (`03-INFRA/scripts/smoke.py` of the candidate checkout) runs while GitHub runs the checks, and its verdict is published on the pull request's head as the `smoke-windows` status (the duration in the description); a failure is published too, and nothing is merged.
   Then it waits for the checks `main`'s branch protection requires, read with `gh api .../branches/main/protection/required_status_checks`. A job nobody requires, `engine-tests-windows` among them, is reported by CI without holding the release; when the protection cannot be read, every check in the run counts as before.
   The `lane-guard` check is ignored here, because it rejects every push that does not come from a feature lane.
4. **merge**: merges with a merge commit.
5. **CI on main**: waits for the same required checks on the merge commit, or (when the protection cannot be read) for the whole `ci.yml` run — unless the merge commit carries exactly the tree the pull request checks passed (`refs/pull/<n>/merge`), in which case those checks are the proof and there is no second CI to wait for.
6. **tag**: creates the tag with `git tag -s`, signed by the key this machine's git is configured to use.
   Its annotation also carries the checks this release was verified against (`Required checks: …`), which `release.yml` reads back: its own token cannot read `main`'s branch protection.
   It then verifies the tag against the trust anchor of the installed copy (`trust/release-signers.txt`).
   If that copy would refuse the tag, the tag is deleted locally and nothing is pushed.
   A tag already on the remote is reused, and a tag left here by an interrupted run is verified and pushed instead of being made again.
7. **release workflow**: pushes the tag, waits for `release.yml`, and reruns it once if it fails.
   It then checks that the GitHub release is not a draft and has assets.
8. **sync developer**: waits for `sync-developer` to merge `main` back into `developer`.
9. **install** (only with `--install`): runs `nexgen update --yes --target vX.Y.Z` through the installed launcher (`~/.local/bin/nexgen.cmd` on Windows, `~/.local/bin/nexgen` elsewhere), never through the `nexgen` that PATH resolves: run from a development checkout that is the checkout, which answers "already at the requested released version" about itself and updates nothing.
   A machine with no installed launcher stops here saying so.
   If the update fails and is rolled back, it retries once; a refusal because the vault has uncommitted or untracked files is not retried, and is reported in one line naming the folder.
   It then checks that the installed `VERSION` matches and runs the installed engine's own smoke (`03-INFRA/scripts/smoke.py` of the installed tree, with the interpreter the launchers use, so a release tree needs no `.venv`): a machine that fails it is put back on the version it came from (the same launcher, `update --yes --target <previous>`) and the release stops saying so.
   Finally it runs the installed launcher's `doctor --summary`, which stops it only on failures (warnings are non-blocking).

Every maintainer machine signs with its own key, and that key has to be listed in `trust/release-signers.txt`.
Installed copies only accept signers they pin, so a new key takes two releases (see `SECURITY.md`).

After a stop, fix the reason and run it again; it resumes from the first step that is not done:
a version that is already bumped is not bumped again, an open release pull request is reused,
a pull request that would change no file is not opened at all,
a release commit already on `main` skips straight to the CI and the tag,
and a tag already published goes on from there.
A rerun while `main` is not yet back in `developer` finishes the release that is out
(the tag is reused, the sync is awaited, `--install` installs the version it finds)
instead of bumping a second version.

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
