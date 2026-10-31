# Sync transaction contract

🇮🇹 [Italiano](it/sync-contract.md)

In a MULTI installation, `nexgen sync` and `nexgen guard` (historical name `agent-sync`) treat propagation as one
guarded transaction. They never regenerate CLI files merely because a pull was attempted: the authoritative data state
must first be proven safe.

## Remote ownership

The data vault owns the remote policy in:

```text
03-INFRA/agent-universal-layer/sync/remotes.yaml
```

Start from `remotes.yaml.example`. `authoritative_remote` is the only remote used to decide whether local data is
fresh, ahead, dirty or diverged. Entries under `mirrors` are publication copies: a stale or unavailable mirror produces
a warning, but never replaces the authoritative history.

`KNOWLEDGE_VAULT_REMOTE` and `KNOWLEDGE_VAULT_MIRRORS` form a complete emergency override. With no file and no
override, the portable default is `origin` with no mirrors. An invalid configuration stops before the provisioner
creates runtime files. Inspect the resolved values with:

```bash
nexgen config authoritative_remote
nexgen config mirrors
```

(Historical name: `agent-sync config authoritative_remote|mirrors`.)

## Commands

Historical names (`agent-sync <mode>`) still work as aliases; the table gives the primary `nexgen` names.

| Command | Contract |
|---|---|
| `nexgen guard` | Recurring pull, apply and healthcheck. Never pushes. A busy lock is a safe skip. |
| `nexgen sync` (alias `nexgen apply`) | Manual pull and apply transaction. Never pushes. |
| `nexgen sync --dry-run` / `nexgen plan` | The plan: what an apply would change, computed without writing, without fetching and without materializing secrets. Exposes `DRIFT` and always exits 0. |
| `nexgen sync --check` / `nexgen plan --check` | The same plan, exiting non-zero when drift is found (CI gate). `nexgen plan --json` dumps the plan with provenance. |
| `nexgen pull` | Pull and healthcheck only. Never regenerates CLI files. |
| `nexgen publish` (alias `nexgen vault push`) | Publishes existing commits to the authoritative remote, then to the configured mirrors. Never pulls or applies. |
| `nexgen preflight` | Validates the local configuration contract without pulling or generating runtime files. |
| `nexgen doctor` | Runs diagnostics and alerts only. |
| `nexgen bootstrap-alerts` | Runs diagnostics and alerts only on FAIL. Internal command, used by the timers. |

## The plan (preview without side effects)

`nexgen plan` is the single plan object shared by preview and dump. It probes drift with the doctor's own read-only
checks (MCP render state, declared skills, starter views, instruction pointers) plus a local-only Git probe (conflicts,
branch, uncommitted work). Two limits are declared in the plan's own output rather than hidden:

- no network. The upstream behind and ahead state cannot be known without a fetch, and a plan never fetches, so preview
  and apply are not perfectly equivalent for the upstream part of the Git domain;
- probes, not simulation. The idempotent self-repair phases (shims, scheduler, modules) are declared, not enumerated.
  On an aligned machine they write nothing.

`--check` turns drift into a non-zero exit for CI; `--json` emits the plan with provenance (engine version, vault,
branch, commit, per-domain findings). The plan never runs remedies, never writes and never touches the network.

Four rules keep the plan honest:

- a probe that cannot run is reported as drift, because apply would fail the same way;
- an undetermined probe is declared under "not checked here" instead of disappearing;
- a warning is never "aligned": a warning that one apply would repair is drift, and one that waits on the user is listed
  under "needs attention";
- a config file absent for a CLI the manifest expects to configure is stated as a planned action
  (`render would create …`), not as a green light.

Running `nexgen` without a command prints help and changes nothing. `sync`, `guard`, `pull` and `preflight` are
separate, explicit commands, so a typo or a forgotten argument cannot combine pull, runtime mutation, credential work
and publication.

## Freshness gate

Apply is allowed only when the local branch matches the authoritative branch, has just fast-forwarded to it, or is
explicitly configured as local-only. It is blocked when the tracked tree is dirty, the remote is missing, fetch fails,
the expected branch is not checked out, the local branch is ahead, the histories diverge, or Git cannot prove their
state.

For a network outage only, a deliberate manual recovery is available:

```bash
nexgen sync --allow-offline
```

`nexgen guard --allow-offline` accepts the same override. It never bypasses dirty, ahead or diverged states, only a
missing remote or a failed fetch.

## Configuration gate

Before generated runtime files are written, `guard` and `sync` run the same preflight as `nexgen preflight`. It loads
the versioned MCP manifest (`mcp/manifest.yaml`) and the skills manifest (`skills.manifest.yaml`) when present, and
fails the transaction if either is not well formed: not a YAML map, or missing its required top-level section.

### Writing the manifest: `nexgen mcp add`

The manifest remains the single canonical declaration. Hand edits stay allowed; `nexgen mcp add <name> --targets
<clis|all>` is the supported way to write one without hand-authoring YAML:

- validated before any write: a safe manifest key, exactly one of `--command` or `--url`, an http(s) URL, single-line
  strings, auth through an environment variable name only, secret-shaped `--env` values refused with the `${VAR}` form,
  and a sensitive variable name with a plain value refused the same way. Flag combinations that would be silently
  dropped, such as `--auth-env` with `--command` or `--env` with `--url`, are errors;
- written through the same locked, atomic path as `--adopt --apply`: a dedicated host lock serializes writers, the
  replacement is a tmpfile plus rename so a crash never truncates the manifest, the result is re-validated by reload, and
  any failure rolls back atomically to the original;
- a name already declared, or listed under `retired_servers`, is refused;
- entries mount by default (`tier: core`). `--lazy` routes them through the proxy instead, and nothing is rendered at
  add time: the next sync or guard regenerates the CLIs from the manifest, exactly as for hand edits.

### Inline per-OS templates

Manifest string values may carry `{{ .os }}`, `{{ .home }}`, `{{ .vault }}`, `{{ .engine }}` and
`{{ if eq .os "windows" }}…{{ else }}…{{ end }}`, so one declaration covers Linux and Windows machines without
duplicating an entry into a `windows:` block when only one path differs. That override remains valid for whole-entry
swaps. The renderer and the lazy proxy expand the same dialect; expansion runs before `${VAR}` expansion, and an unknown
variable or malformed block fails the render closed.

Both manifests carry `schema_version: 1` and are read tolerantly per the forward-compatibility invariant: an individual
connector or skill entry that is malformed, or missing a required field, is skipped with a warning rather than stopping
the whole document. Only a structurally broken manifest stops the transaction, meaning the document itself is not a map
or its required top-level key has the wrong type. The Council seats file (`seats.yaml`) is loaded the same tolerant way,
but only when the `council` command runs; it is not part of the `sync` and `guard` preflight.

This makes a structurally invalid manifest a stop condition before the engine changes a CLI configuration. The preflight
command itself writes only its normal lock and run log.

## Lock and result

One host-wide lock covers the complete operation. A run waits up to 30 seconds to acquire it, long enough to outlast a
normal cycle, so the recurring timer meeting an interactive run waits instead of reporting the machine busy. Override
with `AGENT_SYNC_LOCK_TIMEOUT_SECONDS`. Manual contention past that window exits with code `75`; recurring `guard`
contention exits successfully, because the active run already owns the work. Every declared phase reports success or
failure. Failures are aggregated, later independent checks still run, and the final exit code is non-zero if any
required phase failed.

A successful `sync` or `apply` transaction ends with the message "Alignment completed successfully" (`Allineamento
completato` in Italian). Checking whether the result is actually clean is a separate step: run
`nexgen doctor --strict --summary` after `sync`, because `sync` itself does not run the strict doctor or classify a
readiness verdict.

The publish logic (`nexgen vault push`, historical name `vault-push`) is the `Publisher` class of
`nexgen_core/publisher.py`, not a separate implementation. It locks the same lock file by default
(`AGENT_SYNC_LOCK_FILE`, else `agent-sync.lock` under this host's state directory), so a `guard` cycle and a manual
`nexgen vault push` on the same machine still serialize against each other.

The Linux and Windows launchers call the same Python implementation. Automated tests cover both path dialects and the
Windows lock code, but an architecture change is not operationally complete until it has also run on a physical
Windows installation.

## Configuration layer order

The MCP configuration each CLI receives is the result of a fixed, ordered pipeline inside `nexgen_core/renderer.py`.
The order below is the contract: it is what the generated configs are compared against, and a regression test pins it.
The later a layer runs, the more it wins; a field a later layer does not set stays as the earlier layer produced it.

1. **Canonical manifest** (`mcp/manifest.yaml` in the vault data root), the only authoritative source of servers. A
   server not declared here has no portable source and is reported by the doctor as out-of-manifest, WARN only.
2. **Per-OS override** (a `windows:` block inside a server's manifest entry), applied on Windows only and then
   discarded from the merged view. This is how one manifest serves both OSes without guessing.
3. **Path placeholder expansion** (`${AGENT_ENGINE_ROOT}`, `${AGENT_VAULT_DATA}`, `${KNOWLEDGE_VAULT_PATH}`), resolved
   against the host's actual engine and data roots.
4. **Windows shim normalization**: common interpreter wrapper names (`npx` to `npx.cmd`, `node` to `node.exe`, `python3`
   to `python`) resolved to real paths, and `.cmd` or `.bat` shims routed through `cmd.exe` so every client can launch
   them.
5. **Runtime env placeholder expansion** (`${VAR}` or `${VAR:-default}`), expanded from the host's environment. A server
   gated by `require_env` is omitted entirely when its variable is absent.
6. **Live-config additive preservation**: fields a CLI's own config carries on a managed server but the manifest does
   not declare, such as runtime metadata and client-side overlays, are preserved additively through the surgical
   writers. The merge is shallow on purpose: `env` is one manifest-declared unit, so the manifest's env block wins as a
   whole and a client-side env addition is not carried into the generated config. The client applies its own env
   overlay at runtime.

Invariant: the render path never writes the canonical manifest, except through the explicit `--adopt` flow with backup
and re-validation. The last layer to win is additive live preservation, never the other way around.

## Known limitation

This whole contract is built for one person keeping several machines of their own in sync, not for a team writing to
one shared vault at the same time. The lock above is per machine, not per owner: it is a local file lock under that
machine's own home directory, and it only serializes concurrent processes on that same machine, such as a `guard` cycle
overlapping a manual `nexgen vault push`. It does nothing to arbitrate between one owner's machines running
concurrently, let alone between many people's machines against the same vault.

Concurrent writers are protected by Git's own atomic push instead: a non-fast-forward push is rejected by the remote
outright, never silently overwritten, and the publish path fetches, compares, and either retries with a clean rebase or
aborts and asks for manual resolution on a real conflict. That leaves one gap: two machines editing different,
non-conflicting parts of the same file at nearly the same time can be merged by that automatic rebase with no alert to
either owner. Nothing is lost, but the merge itself is never reviewed. If a team shares one vault as common
infrastructure, concurrent writes from multiple people are not a tested or supported scenario: expect ordinary Git
merge conflicts with no additional tooling in this contract to resolve them. See [team.md](team.md) for why a shared
vault is already a single-user fit problem before sync enters the picture.
