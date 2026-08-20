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
into its own clone separate from your data root. `nexgen update` (see
below) understands both topologies on its own — it is a future "cutover"
path the engine is being built toward, not something `INIT.md` sets up for
you today. If you followed the documented install and never set those
variables, the engine and data clone are simply the same clone, and none of
the split-clone mechanics described below (the `99-INDEX/ENGINE-PIN.txt`
pin) apply to you.

## Why track your version at all

Your data (manifests, instructions, skills) is written against a known
engine behavior. Silently jumping to whatever is newest on `main` every time
you pull could change your running setup without you ever deciding it
should. Your `VERSION` file (`cat VERSION` in your clone) is the version
you're actually running; move it only when you choose to.

## Checking whether an upgrade is available

`nexgen upgrades` (equivalent to `nexgen update --check`) checks this for
you on **both** topologies: it fetches `origin`'s tags (read-only) and
reports whether a released tag newer than your `VERSION` file exists.
Nothing is ever updated automatically; it only tells you the choice exists.
This is a separate command from `nexgen doctor`, which does not check for
engine upgrades at all.

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
nexgen update --check
nexgen update
```

(Historical name, still installed and still working: `nexgen-update`.)

The first command fetches released tags and prints the exact changelog without
moving the branch or changing installed files. The second repeats the checks,
verifies that both the engine clone and the separate data clone are clean,
shows the merge/provision/doctor plan, and asks for confirmation. In
automation, `nexgen update --yes` is the explicit-confirmation form, and
`nexgen update --unattended` additionally refuses any jump larger than a
patch-level release. It discovers the data root in this order:
`AGENT_VAULT_DATA`,
`KNOWLEDGE_VAULT_PATH`, then `~/KnowledgeVault` when that default is a Git
checkout. If none exists, it keeps the engine checkout as the data root. It
uses a fast-forward for a separate consumer engine. In the default single
clone it permits a normal merge commit, so private data commits remain on the
attached branch. When a split data clone already has
`99-INDEX/ENGINE-PIN.txt`, the command commits only that mechanical pin through
`nexgen vault push` (historical name `vault-push`) before provisioning. It
then looks up and runs the `agent-sync` launcher (`agent-sync apply` — the
historical name that `nexgen sync` also answers to), compares the doctor
before and after, and never stashes, resets, or rolls back user work on its
own.

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
     nexgen sync
     nexgen doctor --strict --summary
     ```
     (Historical names, still working: `agent-sync apply`,
     `agent-doctor --strict --summary`.)
     `nexgen sync` first proves that the data branch is fresh against
     the authoritative remote declared in
     `03-INFRA/agent-universal-layer/sync/remotes.yaml`. It then validates
     the configuration contract before it
     writes a generated CLI file. Unsafe Git states and invalid
     configuration stop the sync. See `docs/sync-contract.md`.
   - **MINIMAL profile:** there is no `nexgen sync`/`nexgen doctor` to run —
     per `README.md`, MINIMAL never installs them. Diagnostics are visual:
     open the CLI you configured and confirm it still loads `AGENTS.md`,
     still mounts the MCP servers you expect, and still sees your skills.
5. If `nexgen doctor` reports new `FAIL`s that weren't there before the
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

`agent-doctor` (now `nexgen doctor`) does not compare the deployed
`vault-mcp` server version against the engine version — there is no such
check. `CHANGELOG.md` is the signal for every stack under `deploy/`: if a
release mentions `deploy/`, assume the VPS needs the same tag.

The redeploy is short, and it is documented once, in
[`03-INFRA/deploy/README.md`](../03-INFRA/deploy/README.md) →
"Upgrading the server side": pull the same tag on the VPS, re-run
`bootstrap-vps.sh`. Keep the server on the tag your workstations are on,
not on `main`.

A **Local-Only** install has none of this: no VPS, no `deploy/`, nothing
to upgrade twice.

## If you run Local-Full, your Docker stack is a second install too

The same drift risk applies one machine closer to home. In Local-Full mode
the same connectors — `03-INFRA/deploy/` again — run on this
machine's own Docker instead of a VPS, started with `nexgen stack up`. A
`nexgen sync`/`nexgen update` on this machine upgrades the engine code and
CLI configuration, but it does not restart or redeploy the running
containers: `nexgen doctor` does not compare the running stack's version
against the engine version either — there is no such check, the same as
for Cloud-Server. Watch `CHANGELOG.md` for anything mentioning `deploy/`,
then re-run `nexgen stack up` (it re-applies the current compose
definitions) to bring the containers current; `nexgen stack status` shows
what's actually running.

## Manifest and skill-manifest fields

The MCP manifest and the skills manifest are read tolerantly: an engine
upgrade that adds a new optional field to either schema does not require any
action from you, because an older reader on either side simply ignores a key
it does not recognize rather than rejecting the document (see
`docs/sync-contract.md` → "Configuration gate"). There is no separate,
automatic data-migration mechanism beyond that tolerance today — if a
release ever needs a genuine reshape of an existing field, `CHANGELOG.md`
will say so explicitly and give the manual steps.

## What never happens automatically

- Your `VERSION` never moves by itself.
- `nexgen sync`/`nexgen guard`/`nexgen doctor` never `git pull` or
  `git checkout` your clone.
- Your clone never publishes engine code back upstream. GitHub repository
  controls and CI apply to maintainers publishing changes, not to normal
  private vault usage.

## MCP package pins

The engine runs local MCP packages through exact `npx` versions. The pins live
in `03-INFRA/agent-universal-layer/mcp/manifest.yaml`, with the Antigravity
HTTP bridge pinned in `nexgen_core/renderer.py`.

Do not replace a pin with `latest`. Test one package update in a disposable
setup, run the engine checks, then publish the engine change. If the new
package causes a regression, roll your clone back to the previous tag as
described above.
