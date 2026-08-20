# Sync transaction contract

In a MULTI installation, `nexgen sync`/`nexgen guard` (historical name
`agent-sync`) treats propagation as one guarded transaction. It never
regenerates CLI files merely because a pull command was attempted. The
authoritative data state must first be proven safe.

## Remote ownership

The data vault owns the remote policy in:

```text
03-INFRA/agent-universal-layer/sync/remotes.yaml
```

Start from `remotes.yaml.example`. `authoritative_remote` is the only remote
used to decide whether local data is fresh, ahead, dirty, or diverged. Entries
under `mirrors` are publication copies. A stale or unavailable mirror produces
a warning, but it never replaces the authoritative history.

`KNOWLEDGE_VAULT_REMOTE` and `KNOWLEDGE_VAULT_MIRRORS` form a complete emergency
override. If no file or override exists, the portable default is `origin` with
no mirrors. Invalid configuration stops before the provisioner creates runtime
files. Inspect the resolved values with:

```bash
nexgen config authoritative_remote
nexgen config mirrors
```

(Historical name: `agent-sync config authoritative_remote|mirrors`.)

## Commands

Historical names (`agent-sync <mode>`) still work as aliases; the table
below gives the primary `nexgen` names.

| Command | Contract |
|---|---|
| `nexgen guard` | Recurring pull, apply and healthcheck. Never pushes. A busy lock is a safe skip. |
| `nexgen sync` (alias `nexgen apply`) | Manual pull and apply transaction. Never pushes. |
| `nexgen pull` | Pull and healthcheck only. Never regenerates CLI files. |
| `nexgen publish` (alias: `nexgen vault push`) | Publishes existing commits to the authoritative remote, then configured mirrors. It never pulls or applies. |
| `nexgen preflight` | Validates the local configuration contract without pulling or generating runtime files. |
| `nexgen doctor` | Runs diagnostics and alerts only. |
| `nexgen bootstrap-alerts` | Runs diagnostics and alerts only on FAIL (internal command, used by the timers). |

Running `nexgen` without a command prints help and changes nothing. `sync`,
`guard`, `pull`, and `preflight` are separate, explicit commands, so a typo
or forgotten argument cannot combine pull, runtime mutation, credential
work, and publication.

## Freshness gate

Apply is allowed only when the local branch matches the authoritative branch,
has just fast-forwarded to it, or is explicitly configured as local-only. It is
blocked when the tracked tree is dirty, the remote is missing, fetch fails, the
expected branch is not checked out, the local branch is ahead, the histories
diverge, or Git cannot prove their state.

A deliberate manual recovery is available for a network outage only:

```bash
nexgen sync --allow-offline
```

(`nexgen guard --allow-offline` accepts the same override; it never bypasses
dirty, ahead, or diverged states — only a missing remote or a failed fetch.)

## Configuration gate

Before generated runtime files are written, `guard` and `sync` run the same
preflight as `nexgen preflight`. It loads the versioned MCP manifest
(`mcp/manifest.yaml`) and the skills manifest (`skills.manifest.yaml`) when
present, and fails the transaction if either is not well-formed (not a
YAML map, or missing its required top-level section).

Both manifests carry `schema_version: 1` and are read tolerantly per the
forward-compatibility invariant: an individual connector or skill entry that
is malformed, or missing a required field, is skipped with a warning rather
than stopping the whole document; only a structurally broken manifest (the
document itself is not a map, or its required top-level key is the wrong
type) stops the transaction. The Council seats file (`seats.yaml`) is loaded
the same tolerant way, but only when the `council` command runs — it is not
part of the `sync`/`guard` preflight.

This makes a structurally invalid manifest a stop condition before the engine
changes a CLI configuration. The preflight command itself writes only its
normal lock and run log.

## Lock and result

One host-wide lock covers the complete operation. A run waits up to 30 seconds
to acquire it — long enough to outlast a normal cycle, so the recurring timer
meeting an interactive run waits for it instead of reporting the machine busy.
Override with `AGENT_SYNC_LOCK_TIMEOUT_SECONDS`. Manual contention past that
window exits with code `75`; recurring `guard` contention exits successfully
because the active run already owns the work. Every declared phase reports
success or failure.
Failures are aggregated, later independent checks still run, and the final exit
code is non-zero if any required phase failed. A successful `sync`/`apply`
transaction ends with the message "Allineamento completato con successo"
(alignment complete). Checking whether the result is actually clean is a
separate step: run `nexgen doctor --strict --summary` after `sync` — `sync`
itself does not run the strict doctor or classify a readiness verdict.

The publish logic (`nexgen vault push`, historical name `vault-push`) is the
`Publisher` class of `nexgen_core/publisher.py` — not a separate
implementation. It locks the same lock file by default
(`AGENT_SYNC_LOCK_FILE`, else `agent-sync.lock` under this same host's state
directory), so a `guard` cycle and a manual `nexgen vault push` on the same
machine still serialize against each other.

The Linux and Windows launchers call the same Python implementation. Automated
tests cover both path dialects and Windows lock code, but an architecture
change is not operationally complete until it has also been exercised on a
physical Windows installation.

## Configuration layer order

The MCP configuration each CLI receives is the result of a fixed, ordered
pipeline inside `nexgen_core/renderer.py`. The order below is the contract: it is what the
generated configs are compared against, and a regression test pins it. The
later a layer runs, the more it wins; a field a later layer does not set is
simply left as the earlier layer produced it.

1. **Canonical manifest** (`mcp/manifest.yaml` in the vault data root): the
   only authoritative source of servers. A server not declared here has no
   portable source and is reported by the doctor as out-of-manifest
   (WARN-only).
2. **Per-OS override** (a `windows:` block inside a server's manifest entry):
   applied on Windows only, then discarded from the merged view. This is how
   one manifest serves both OSes without guessing.
3. **Path placeholder expansion** (`${AGENT_ENGINE_ROOT}`,
   `${AGENT_VAULT_DATA}`, `${KNOWLEDGE_VAULT_PATH}`): resolved against the
   host's actual engine/data roots.
4. **Windows shim normalization**: common interpreter wrapper names
   (`npx` -> `npx.cmd`, `node` -> `node.exe`, `python3` -> `python`) resolved
   to real paths, and `.cmd`/`.bat` shims routed through `cmd.exe` so every
   client can launch them.
5. **Runtime env placeholder expansion** (`${VAR}` / `${VAR:-default}`):
   expanded from the host's environment; a server gated by `require_env` is
   omitted entirely when its env var is absent.
6. **Live-config additive preservation**: fields that a CLI's own config
   carries on a managed server but the manifest does not declare (runtime
   metadata, client-side overlays) are preserved additively through the
   surgical writers. The merge is shallow on purpose: `env` is treated as
   one manifest-declared unit, so the manifest's env block wins as a whole
   and a client-side env addition is not carried into the generated config
   (the client applies its own env overlay at runtime).

Invariant: the canonical manifest is never written by the render path except
through the explicit `--adopt` flow (with backup + re-validation), and the
last layer to win is the additive live preservation, never the other way
around.

## Known limitation

This whole contract is built for one person keeping several machines of
their own in sync, not for a team writing to one shared vault at the same
time. The lock described above (see "Lock and result") is per-machine, not
per-owner: it is a local file lock under that machine's own home directory,
and it only serializes concurrent processes running ON THAT SAME machine
(e.g. a `guard` cycle overlapping a manual `nexgen vault push`). It does
nothing to arbitrate between a single owner's own several machines running
concurrently, let alone between many different people's machines against
the same vault. Concurrent writers are instead protected by git's own
atomic push: a non-fast-forward push is rejected by the remote outright,
never silently overwritten, and the publish path fetches, compares, and
retries with a clean rebase or aborts and asks for manual resolution on a
real conflict. The one gap that leaves open: two machines editing
different, non-conflicting parts of the same file at nearly the same time
can be merged by that automatic rebase with no alert to either owner --
nothing is lost, but the merge itself is never reviewed. If a team shares
one vault as common infrastructure (see `docs/team.md` for why that's
already a mono-user fit problem before sync even enters the picture),
concurrent writes from multiple people are not a tested or supported
scenario today: expect ordinary Git merge conflicts with no additional
tooling in this contract to resolve them.
