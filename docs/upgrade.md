# Upgrading the engine

The install documented in `README.md`/`INIT.md` is a single `git clone` into
one folder (e.g. `~/KnowledgeVault`). That one clone plays both roles at
once: it is the engine code you run (`03-INFRA/`, the scripts, the docs) and
the data root where your own notes, `99-INDEX/USER-PROFILE.md`, and
manifests live. There is no separate "engine-only" clone by default —
everything below matches that single-clone topology, because that is what
the documented install actually produces.

The codebase does contain the plumbing for a second, more advanced
topology: `AGENT_ENGINE_ROOT`/`AGENT_VAULT_DATA` let you split the engine
into its own clone (referred to internally as the "consumer engine clone")
separate from your data root, and `agent-doctor` has a version-pin check
(section S2) plus a new-version-available warning that key off that split
clone's `.git`. That section only activates once you have deliberately
created a second clone and pointed those variables at it; it is a future
"cutover" path the engine is being built toward, not something `INIT.md`
sets up for you today. If you followed the documented install and never set
those variables, that section of `agent-doctor` stays silent, and none of
the `~/.nexgen-engine`-style pin mechanics described below apply to you.

## Why track your version at all

Your data (manifests, instructions, skills) is written against a known
engine behavior. Silently jumping to whatever is newest on `main` every time
you pull could change your running setup without you ever deciding it
should. Your `VERSION` file (`cat VERSION` in your clone) is the version
you're actually running; move it only when you choose to.

## Checking whether an upgrade is available

`agent-doctor` checks this for you on **both** topologies: on the default
single-clone install it fetches `origin`'s tags (read-only, bounded) and
warns — informationally, never a FAIL — when a released tag newer than your
`VERSION` file exists ("new engine version available: vX.Y.Z"). Nothing is
ever updated automatically; the warning just tells you the choice exists.
A vault whose `origin` is your own private data remote (no engine tags) or
that has no `VERSION` file skips the check silently.

To check by hand instead:

```bash
cd ~/KnowledgeVault   # or wherever you cloned it
git fetch --tags origin
git tag --merged origin/main --sort=-v:refname | head -1   # latest released tag
cat VERSION                                                  # the version you're actually running
```

If the latest tag is newer than your `VERSION` file, an upgrade is
available.

## Upgrading

The normal path is now the real cross-platform command installed by the
MULTI provisioner:

```bash
nexgen-update --check
nexgen-update
```

The first command fetches released tags and prints the exact changelog without
moving the branch or changing installed files. The second repeats the checks,
verifies that both the engine clone and the separate data clone are clean,
shows the merge/provision/doctor plan, and asks for confirmation. In
automation, `nexgen-update --yes` is the explicit-confirmation form. It
uses a fast-forward for a separate consumer engine. In the default single
clone it permits a normal merge commit, so private data commits remain on the
attached branch. When a split data clone already has
`99-INDEX/ENGINE-PIN.txt`, the command commits only that mechanical pin through
`vault-push` before provisioning. It then runs `agent-sync apply`, compares the
doctor before and after, and never stashes, resets, or rolls back user work on
its own.

The manual sequence below remains the recovery path for MINIMAL installs and
for the one bootstrap upgrade from a version that predates the command.

1. Read `CHANGELOG.md` for the version(s) between your current `VERSION`
   and the one you're moving to. Pay attention to the `### Changed` and
   `### Removed` sections — `### Added` is always safe.
2. Make sure your working tree is clean (`git status`). Your own vault
   content — `01-NOTES/`, `02-PROJECTS/`, `99-INDEX/USER-PROFILE.md`,
   `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml`, and so on —
   lives as ordinary commits in this same clone, so commit or stash
   anything in progress before moving the ref.
3. Fetch and bring in the target tag:
   ```bash
   git fetch --tags origin
   git merge vX.Y.Z
   ```
   Prefer `merge` over `git checkout vX.Y.Z`: a bare checkout detaches
   `HEAD` and leaves any local commits you made on `main` (your notes,
   your profile) stranded off the branch. A merge keeps them on `main` and
   only asks you to resolve a conflict if the new engine version and one of
   your own edits touched the exact same file — which shouldn't happen if
   you've kept customization inside your own data (notes, manifests,
   `USER-PROFILE.md`) rather than hand-editing engine-owned scripts.
4. Run a provisioning pass and check the result:
   - **MULTI profile:**
     ```bash
     agent-sync apply
     agent-doctor --strict --summary
     ```
     `agent-sync apply` first proves that the data branch is fresh against
     the authoritative remote declared in
     `03-INFRA/agent-universal-layer/sync/remotes.yaml`. It then validates
     the configuration contract before it runs any pending data migration or
     writes a generated CLI file. Unsafe Git states and invalid
     configuration stop the apply. See `docs/sync-contract.md`.
   - **MINIMAL profile:** there is no `agent-sync`/`agent-doctor` to run —
     per `README.md`, MINIMAL never installs them. Diagnostics are visual:
     open the CLI you configured and confirm it still loads `AGENTS.md`,
     still mounts the MCP servers you expect, and still sees your skills.
5. If `agent-doctor` reports new `FAIL`s that weren't there before the
   upgrade (MULTI), or your CLI stops behaving the way it did before
   (MINIMAL), something in the new version doesn't fit your setup. Roll
   back with `git reset --hard <previous-commit-or-tag>`, then report what
   broke.

## If you run a Cloud-Server install, that VPS is a second install

Everything above upgrades the machine you typed it on. It does not touch
your server. `03-INFRA/deploy/` — `vault-mcp` (the `vault-library` MCP
write door), the OCR API, Firecrawl, n8n — is engine code that happens to
run somewhere else, and the VPS keeps running the containers it was last
deployed with. Nothing on your workstation can restart them for you, and
for a long time nothing even told you they were behind.

Now `agent-doctor` does, for the one service that reports its own version:

```text
⚠ vault-mcp on the server is 0.3.0 but this engine ships 0.4.0 —
  the server half of the upgrade was never deployed
```

It is a WARN and never a FAIL: a lagging server keeps working, and when to
take it down is your call, not the tool's. For the other three stacks
`CHANGELOG.md` is the signal — if a release mentions `deploy/`, assume
the VPS needs the same tag.

The redeploy is short, and it is documented once, in
[`03-INFRA/deploy/README.md`](../03-INFRA/deploy/README.md) →
"Upgrading the server side": pull the same tag on the VPS, re-run
`bootstrap-vps.sh`. Keep the server on the tag your workstations are on,
not on `main`.

A **Local-Only** install has none of this: no VPS, no `deploy/`, nothing
to upgrade twice.

## Data migrations

Some engine releases may need to reshape a data file (a manifest field
renamed, a new required key). When that happens, `agent-sync apply` runs
the needed migration automatically, in order, the first time it sees your
data at an older schema version:

- Before writing anything, it backs up the affected file next to itself as
  `<file>.bak-<timestamp>` (same convention as the config backups you'll
  already have seen from `render.py`; the last 3 are kept).
- Each migration is idempotent: running it again on already-migrated data
  is a no-op.
- Your data schema version is tracked in
  `99-INDEX/DATA-SCHEMA-VERSION.txt` (in your data root — this file is
  yours, not published with the engine).
- If your data is already at the schema the engine expects, this step does
  nothing at all — no file is touched, no backup is created.

This runs only as part of `agent-sync apply`, so it is a MULTI-profile
mechanism. There are no migrations registered yet as of the current engine
`VERSION` — today's data shape is still the baseline this mechanism starts
counting from, for both profiles. A migration runs only after the preflight
has accepted the data source, and before runtime files are generated.

## What never happens automatically

- Your `VERSION` never moves by itself.
- `agent-sync`/`agent-doctor` never `git pull` or `git checkout` your
  clone.
- Your clone never publishes engine code back upstream. GitHub repository
  controls and CI apply to maintainers publishing changes, not to normal
  private vault usage.
- A data migration never runs against a schema version newer than what the
  installed engine understands — if that happens (e.g. you rolled the
  engine back), `agent-sync` leaves your data untouched and logs why.

## MCP package pins

The engine runs local MCP packages through exact `npx` versions. The pins live
in `03-INFRA/agent-universal-layer/mcp/manifest.yaml`, with the Antigravity
HTTP bridge pinned in `mcp/render.py`.

Do not replace a pin with `latest`. Test one package update in a disposable
setup, run the engine checks, then publish the engine change. If the new
package causes a regression, roll your clone back to the previous tag as
described above.
