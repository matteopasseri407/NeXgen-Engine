# Upgrading the engine

🇮🇹 [Italiano](it/upgrade.md)

This guide covers Git installations. If you installed the package with `uv` or `pipx`, update it through that package
manager: `nexgen update` needs an engine Git checkout.

One clone, such as `~/KnowledgeVault`, can hold both the engine code and your private data. A split installation keeps
them in separate clones, selected by `AGENT_ENGINE_ROOT` and `AGENT_VAULT_DATA`. The updater handles both, and a split
installation records its engine version in `99-INDEX/ENGINE-PIN.txt`.

If you are adding an existing PC to a central gateway, see
[Machine roles](roles.md#already-have-nexgen-on-this-pc).

## Why track your version at all

Your data (manifests, instructions, skills) is written against a known engine behaviour. Pulling whatever is newest on
`main` every time would change your running setup without you deciding it should. The `VERSION` file in your clone,
which `cat VERSION` prints, is the version you actually run; move it only when you choose to.

## Checking whether an upgrade is available

`nexgen upgrades`, equivalent to `nexgen update --check`, works on both topologies: it fetches `origin`'s tags,
read-only, and reports whether a released tag newer than your `VERSION` exists. Check mode leaves the installed version
untouched. The scheduled heartbeat separately attempts unattended updates within the patch-release ceiling.
`nexgen doctor` checks alignment; the upgrade command is what tells you about a newer release.

To check by hand:

```bash
cd ~/KnowledgeVault   # or wherever you cloned it
git fetch --tags origin
git tag --merged origin/main --sort=-v:refname | head -1   # latest released tag
cat VERSION                                                  # the version you're actually running
```

If the latest tag is newer than `VERSION`, an upgrade is available.

## Channels

Development builds are for people who work on the engine. The default is `stable`, which follows released tags of
`main`. An installation opens `nightly` or `beta` in its own private config (`update.allow_channels: [nightly, beta]`
in `sync/remotes.yaml`) and remembers the choice with `nexgen update --channel nightly`. `nightly` follows the tip of
`developer` only when it is signed and its CI gates (leak scan, Linux suite, fast Windows gate) are green on that
commit. `beta` follows only the newest signed commit the full nightly suite has proved on Windows and Linux.

## Startup notice (one prompt per day, launch or skip)

Every interactive shell can tell you an upgrade is pending and offer to launch it. The guard installs both lanes on
every apply, on Linux and Windows: a shell hook plus a boot-time check, which is XDG autostart and a boot timer on
Linux and a logon task on Windows. Install or remove them by hand with:

```bash
nexgen tool update-notifier --install-shell-hook   # bash + PowerShell
nexgen tool update-notifier --install-autostart    # graphical dialog lane (Linux)
```

Graphical prompts carry the engine logo (`assets/nexgen-logo.jpg`, resolved from the checkout when the dialog opens,
with the stock icon as fallback when the checkout ships without assets): Linux zenity dialogs through `--window-icon`
and `notify-send` through `--icon`, Windows passive toasts through BurntToast `-AppLogo`. The logo rides notifications
that are already being shown, and no fixed icon is installed anywhere. Blocking `MessageBox` prompts stay text only,
because that API cannot carry a custom image.

Each new shell then runs `--shell-check`: a cache read, never the network, and a stale cache refreshes detached in the
background. When an upgrade is pending you get one line plus `Aggiorna ora? [s/N]`, at most once per day per version.
"Yes" hands the terminal to the interactive `nexgen update`, release notes first and its own confirmation second.
"No" stays silent until tomorrow. The guard cycle and the hourly heartbeat refresh the cache with a read-only tag
lookup, silent when offline, so the prompt never holds a shell startup hostage. Remove it with
`nexgen tool update-notifier --install-shell-hook --remove`.

## Upgrading

The normal path is the cross-platform command the MULTI provisioner installs:

```bash
nexgen update --check
nexgen update
```

(Historical name, still installed and still working: `nexgen-update`.)

The first command fetches released tags and prints the changelog without moving the branch or changing installed files.
The second repeats the checks, verifies that both the engine clone and the separate data clone are clean, shows the
merge, provision and doctor plan, and asks for confirmation. In automation, `nexgen update --yes` is the explicit
confirmation, and `nexgen update --unattended` additionally refuses any jump larger than a patch-level release. A
maintainer machine can raise that to minor releases with `nexgen update --set-unattended-ceiling minor`, stored in this
machine's state directory; `patch` puts it back. A major release is never taken unattended.

It discovers the data root in this order: `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`, then `~/KnowledgeVault` when that
default is a Git checkout. If none exists, it keeps the engine checkout as the data root. It uses a fast-forward for a
separate consumer engine. In the default single clone it allows a normal merge commit, so private data commits stay on
the attached branch. When a split data clone already has `99-INDEX/ENGINE-PIN.txt`, the command commits only that
mechanical pin through `nexgen vault push` (historical name `vault-push`) before provisioning. It then looks up and runs
the `agent-sync` launcher (`agent-sync apply`, the historical name `nexgen sync` also answers to), compares the doctor
before and after, and never stashes, resets or rolls back user work on its own.

Before merging, it verifies the release tag's signature against the signers pinned in the copy you already have
installed (see [SECURITY.md](../SECURITY.md)). A wrong signature, or one by a key that copy does not pin, stops the
update in every mode with exit `70`. A release it cannot verify at all, because there is no signature or no verification
tool on the machine, installs anyway with one plain line saying so, in every mode including `--unattended`: a machine
that cannot check still needs its updates, and a question the installer cannot evaluate would only teach people to
confirm blindly. The merge waits for a running sync to finish instead of merging underneath it.

`--unattended` is the one mode that undoes its own work. If the update fails after the engine moved, it goes back to
the version that was working, restores the engine pin, remembers the release as rejected so the next heartbeat does not
retry it every hour, and raises an alert. Run `nexgen update --target <version>` yourself to try that release again.
Exit codes: `70` refused for its signature, `71` failed and was undone, `72` failed and could not be undone, with the
manual recovery printed.

### The engine's own Python environment

LangGraph, LangChain and `mcp` are core dependencies: the Council's resumable relay, the local lane and the MCP servers
run on them, and the system `python3` usually has none of them. For a checkout the guard therefore provisions an
environment of its own, `~/.local/share/nexgen-engine/runtime`, outside every git tree so it never makes the checkout
dirty, from the dependency list in `pyproject.toml`. The launchers use it as soon as it has been provisioned and
verified, and fall back to any suitable Python until then, so the guard and the doctor still need nothing but PyYAML.
`nexgen runtime check` says whether the interpreter you are running can import everything, `nexgen runtime ensure`
creates or repairs the environment by hand, and `nexgen doctor` reports a missing library as broken with that command. A
package install is its own environment and is only verified.

The manual sequence below remains the recovery path for MINIMAL installs and for the one bootstrap upgrade from a
version that predates the command.

1. Read `CHANGELOG.md` for the versions between your current `VERSION` and the one you are moving to. Pay attention to
   `### Changed` and `### Removed`; `### Added` is always safe.
2. Make sure your working tree is clean (`git status`). Your own vault content, such as `01-NOTES/`, `02-PROJECTS/`,
   `99-INDEX/USER-PROFILE.md` and `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml`, lives as ordinary
   commits in the same clone, so commit or stash anything in progress before moving the ref.
3. Fetch and bring in the target tag:
   ```bash
   git fetch --tags origin
   git merge vX.Y.Z
   ```
   Prefer `merge` over `git checkout vX.Y.Z`: a bare checkout detaches `HEAD` and leaves your local commits on `main`,
   your notes and your profile, stranded off the branch. A merge keeps them on `main` and asks you to resolve a
   conflict only if the new engine version and one of your edits touched the same file, which should not happen if you
   keep customization in your own data (notes, manifests, `USER-PROFILE.md`) instead of hand-editing engine scripts.
4. Run a provisioning pass and check the result.
   - MULTI profile:
     ```bash
     nexgen sync
     nexgen doctor --strict --summary
     ```
     (Historical names, still working: `agent-sync apply`, `agent-doctor --strict --summary`.)
     `nexgen sync` first proves that the data branch is fresh against the authoritative remote declared in
     `03-INFRA/agent-universal-layer/sync/remotes.yaml`. It then validates the configuration contract before writing a
     generated CLI file. Unsafe Git states and invalid configuration stop the sync. See [sync-contract.md](sync-contract.md).
   - MINIMAL profile: there is no `nexgen sync` or `nexgen doctor` to run, since MINIMAL never installs them.
     Diagnostics are visual: open the CLI you configured and confirm it still loads `AGENTS.md`, still mounts the MCP
     servers you expect and still sees your skills.
5. If `nexgen doctor` reports new `FAIL`s that were not there before the upgrade (MULTI), or your CLI stops behaving
   as it did (MINIMAL), something in the new version does not fit your setup. Roll back with
   `git reset --hard <previous-commit-or-tag>` and report what broke.

## If you run a Cloud-Server install, that VPS is a second install

Everything above upgrades the machine you typed it on. It does not touch your server. `03-INFRA/deploy/` (the
`vault-mcp` write door that `vault-library` speaks to, the OCR API, n8n) is engine code that happens to run elsewhere,
and the VPS keeps running the containers it was last deployed with. Nothing on your workstation can restart them for
you.

`nexgen doctor` does not compare the deployed `vault-mcp` server version against the engine version; there is no such
check. `CHANGELOG.md` is the signal for every stack under `deploy/`: if a release mentions `deploy/`, assume the VPS
needs the same tag.

The redeploy is short and documented once in [`03-INFRA/deploy/README.md`](../03-INFRA/deploy/README.md) under
"Upgrading the server side": pull the same tag on the VPS and re-run `bootstrap-vps.sh`. Keep the server on the tag
your workstations are on, not on `main`.

A Local-Only install has none of this: no VPS, no `deploy/`, nothing to upgrade twice.

## If you run Local-Full, your Docker stack is a second install too

The same drift risk applies one machine closer to home. In Local-Full mode the same connectors under
`03-INFRA/deploy/` run on this machine's own Docker instead of a VPS, started with `nexgen stack up`. A `nexgen sync` or
`nexgen update` here upgrades the engine code and the CLI configuration, but does not restart or redeploy the running
containers, and `nexgen doctor` does not compare the running stack's version against the engine version either; there
is no such check, as for Cloud-Server. Watch `CHANGELOG.md` for anything mentioning `deploy/`, then re-run
`nexgen stack up`, which re-applies the current compose definitions, to bring the containers current. `nexgen stack
status` shows what is actually running.

## Manifest and skill-manifest fields

The MCP manifest and the skills manifest are read tolerantly: an engine upgrade that adds a new optional field to
either schema needs no action from you, because an older reader on either side ignores a key it does not recognise
instead of rejecting the document (see [sync-contract.md](sync-contract.md), "Configuration gate"). There is no separate
automatic data-migration mechanism beyond that tolerance today. If a release ever needs a genuine reshape of an
existing field, `CHANGELOG.md` will say so explicitly and give the manual steps.

## When a module retires

A module can leave the catalog in a release. Firecrawl did: the connector is the vendor's cloud API now, with your own
key, so there is nothing left to place. The derivation ignores a `modules.state.yaml` entry whose module is no longer in
the catalog, and `nexgen doctor` reports it once, as a warning, with the module name. Drop that line from
`03-INFRA/agent-universal-layer/modules.state.yaml` when you see it; nothing else depends on it. A retired connector's
server also disappears from the rendered CLI configs on the next sync, and its key is simply not needed any more.

## What never happens automatically

- Your `VERSION` never moves by itself.
- `nexgen sync`, `nexgen guard` and `nexgen doctor` never `git pull` or `git checkout` your clone.
- Your clone never publishes engine code back upstream. GitHub repository controls and CI apply to maintainers
  publishing changes, not to normal private vault usage.

## MCP package pins

The engine runs local MCP packages through exact `npx` versions. The pins live in
`03-INFRA/agent-universal-layer/mcp/manifest.yaml`, with the Antigravity HTTP bridge pinned in
`nexgen_core/renderer.py`.

Do not replace a pin with `latest`. Test one package update in a disposable setup, run the engine checks, then publish
the engine change. If the new package causes a regression, roll your clone back to the previous tag as described above.
