# What gets written

This lists every file the installer (`INIT.md`), `install.sh`, and the MULTI-profile scripts (`agent-sync`, `render.py`) can create or modify outside this repo. Nothing here touches files outside your home directory, and nothing runs with elevated privileges.

## `install.sh`

`bash install.sh --check` is strictly read-only: a missing scaffold folder is reported as a failed check (`✗`), never created.

Plain `bash install.sh` (the default, guided mode) is almost as quiet: the only thing it can write is missing scaffold folders inside the repo itself (`01-NOTES/`, `02-PROJECTS/`, `04-NOW/`, `99-INDEX/`, `99-SECRETS/`, each with a `.gitkeep`), and only if they're missing from your clone — which normally does not happen on a full clone. It never writes outside the repo and asks no questions it doesn't discard afterward (the guided profile interview at the end is recommendation-only; nothing you answer is persisted by this script).

## `INIT.md` (the AI-guided installer, any profile)

- `99-INDEX/USER-PROFILE.md`: your profile, hardware, CLI/machine list, and architecture choice.
- Optionally `04-NOW/current-focus.md` or a note under `01-NOTES/`/`02-PROJECTS/`, if you choose to have it ingest a document (CV, project brief, brand rules) during setup.

## Per-CLI bootstrap and MCP config (MINIMAL: done by the agent by hand; MULTI: done by `agent-sync`/`render.py`)

| CLI | Bootstrap file | MCP config | Skills folder |
|---|---|---|---|
| Claude Code | `~/CLAUDE.md` (pointer to this repo's `AGENTS.md`) | `mcpServers` field in `~/.claude.json` | declared native-lazy view in `~/.claude/skills/`, per skill with `claude` in `targets` |
| Codex | `~/.codex/AGENTS.md` | Codex's own config file | declared native-lazy view in `~/.codex/skills/` (or `$CODEX_HOME/skills`, its only skill root), per skill with `codex` in `targets` — independent of `exposure` |
| OpenCode | `instructions` field in `opencode.json` | MCP section of the same `opencode.json` | `agent-skill find|show`, backed by `~/.agents/skill-library/` |
| Antigravity | `~/.gemini/config/AGENTS.md` | `~/.gemini/antigravity/mcp_config.json`\* | declared native view in `~/.gemini/antigravity-cli/skills/`, per skill with `antigravity` in `targets` |

MCP sections are additive by default. A server is removed from generated CLI
configs only when its exact old name is deliberately added to the canonical
manifest's `retired_servers` list. Authenticated Antigravity HTTP entries use
the engine-owned `mcp-http-bridge.mjs`; generated JSON stores only the bearer
environment-variable name, never its value.

\* `~/.gemini/antigravity/mcp_config.json` is only the canonical source `render.py`
generates. `agent-sync`'s `antigravity_mcp` phase fans it out (via symlink, or a
real file copy on Windows when the symlink/junction privilege is unavailable —
see `make_link()` in `agent_sync.py`) to three further paths: `~/.gemini/antigravity-cli/mcp_config.json`,
`~/.gemini/antigravity-ide/mcp_config.json`, and `~/.gemini/config/mcp_config.json`.
The last one is what `agent-doctor` validates as the path the live Antigravity
CLI actually reads (both a config-content check and a real `agy` invocation).

These are patches to files that must already exist (each CLI creates its own default config the first time you open it). Nothing here creates a CLI's config file from scratch; if a chosen CLI has never been opened, that step is skipped for it.

## MULTI profile only, additional writes by `agent-sync`

- `~/.config/systemd/user/agent-sync.service` and `agent-sync.timer`: a recurring user-level timer that runs `agent-sync guard` (pull + regenerate CLI runtime files + healthcheck, no push). Only on Linux/systemd.
- Before overwriting the content of a file it merges into — Claude's `settings.json` (hook/permission merges), the systemd unit files above, OpenCode's `instructions` field or its `"plugin"` array, and Antigravity's `~/.gemini/config/hooks.json` — `agent-sync` copies the previous version alongside it with a `.pre-<reason>-<timestamp>.bak` suffix in the same folder. These are **not** rotated: one file is kept per change, and nothing deletes the older ones, so they accumulate over time and are yours to clean up. (Rotation — newest three kept — applies to the differently-named `<file>.bak-<timestamp>` copies that `render.py` writes for MCP config changes, not to these.) A second, separately-named backup convention (`<file>.local-edit.bak-<timestamp>`) belongs to the shared symlink/junction helper that fans out desktop entries, PATH shims, and generated config copies: it fires only when that helper finds an existing real (non-symlink) copy whose content has diverged from the canonical source, which as of this writing only happens on Windows, where a missing symlink/junction privilege can leave such a copy behind in the first place. Verify `make_link()` in `agent_sync.py` before relying on that second case, since cross-platform coverage for it may since have changed.
- `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` **in your own data root**, and only when that file does not exist at all: a verbatim copy of the `skills.manifest.yaml.example` this repo ships, so the seven starter commands (`/vault-doctor` … `/nexgen-update`) actually exist after an install instead of only in the README. This is the one place `agent-sync` creates a file inside your vault rather than in a runtime directory. It never rewrites an existing manifest, and it does nothing at all when the skill bodies it declares aren't present in that data root (a split engine/data topology), so emptying the file to `skills: {}` is a permanent opt-out.
- `~/.local/bin/nexgen-update` on Linux and macOS, or `nexgen-update.ps1` plus `nexgen-update.cmd` on Windows: the real updater command. These launchers call the same engine-owned Python implementation. The command fetches public release tags and does not move the installed branch until it has shown the plan and received confirmation. In a split topology with an existing `99-INDEX/ENGINE-PIN.txt`, the confirmed transaction updates and commits only that pin through `vault-push` before provisioning.
- `~/.local/state/agent-sync.log`: a plain-text run log.
- `~/.local/state/agent-sync.lock`: the stable one-byte host-wide transaction lock.
- `~/ANTIGRAVITY.md`: removed if present as a dead symlink (Antigravity doesn't read that path).
- `~/.claude/settings.json`: Claude's own settings file, patched additively. `agent-sync` merges its `SessionStart`/`PreCompact` checkpoint hook here, and copies the hook body next to it as `~/.claude/claude-vault-checkpoint.mjs`. If — and only if — your private vault declares `agent-universal-layer/permissions/manifest.yaml`, the `claude_permissions` phase also writes `permissions.defaultMode`, any guardrail hook bodies it declares, and (for the `bypass` posture) `skipDangerousModePermissionPrompt`. With no such manifest, which is the default for every installation, that phase writes nothing at all.
- **OpenCode and Antigravity guardrail hooks** (same manifest, same phase — a manifest guardrail hook can target `opencode`/`antigravity` in addition to, or instead of, `claude`; a posture naming one of the two is refused if its own declared guardrail can't be installed, same ordering as Claude above): each CLI's native hook contract differs from Claude's, so a thin, engine-owned adapter translates it to/from the same stdin/stdout JSON shape a Claude guardrail body already speaks — the guardrail body itself stays the same private vault file for every CLI it targets.
  - OpenCode: the adapter is copied to `<opencode config dir>/nexgen-guardrail-plugin.mjs` and registered (appended, never replacing existing entries) in that config's own `"plugin"` array. Each declared guardrail body is copied to `<opencode config dir>/nexgen-guardrail-hooks/`, and a sidecar `<opencode config dir>/nexgen-guardrail.config.json` lists them for the plugin to read.
  - Antigravity: the adapter is copied to `~/.gemini/config/nexgen-guardrail-adapter.mjs` and registered as a `PreToolUse` entry (key `nexgen-guardrail`) in `~/.gemini/config/hooks.json` — every other top-level hook name already in that file, yours or another tool's, is left untouched. Each declared guardrail body is copied to `~/.gemini/config/nexgen-guardrail-hooks/`, with the same kind of sidecar `nexgen-guardrail.config.json` alongside the adapter.
  - Codex is not included: its own hook mechanism gates every hook behind a persisted, per-hash trust prompt (or an explicit `--dangerously-bypass-hook-trust` flag) that this engine cannot satisfy on its own, so declaring a Codex guardrail hook remains a hard manifest error rather than a half-working install.

### Linux only: two desktop entries, one of which shadows your Chrome launcher

On Linux, `agent-sync` writes both of these under `~/.local/share/applications`:

- `agent-chrome.desktop`: a new launcher for the visible shared Chrome. Adding it does **not** make it your default browser; that stays your own choice.
- `google-chrome.desktop`: a hidden entry that **shadows your distribution's Chrome launcher** in your user XDG layer. After this, existing dock icons and anything activating `google-chrome.desktop` start the wrapper instead of plain Chrome.

The second one is deliberate: a plain Chrome started from the dock wins the first-process race with no CDP port open, and the shared-browser lane silently stops working. It is per-user, reversible, and removed by the uninstall steps; nothing outside your home directory is touched.

### Windows equivalent: scheduled task and hidden wrapper

On Windows, every `agent-sync apply`/`guard` run (`install_scheduler` in
`agent_sync.py`) self-heals the same recurring trigger that systemd provides
on Linux, using only your own user account (no admin elevation, no service):

- `~/.local/bin/<command>.ps1`: a real, generated shim that invokes the
  engine-owned PowerShell script by absolute path. A file symlink is not used,
  because PowerShell would resolve `$PSScriptRoot` to the link directory.
- `~/.local/bin/<command>.cmd`: the bare-command wrapper that invokes that
  local PowerShell shim.
- A hidden VBS wrapper, `start-agent-sync-hidden.vbs`, is written under the
  user's runtime state (`~/.local/state/`). It preserves the resolved engine,
  vault-data, vault, and branch values, then shells out to
  `agent-sync.ps1 guard` via `powershell.exe -NoProfile -ExecutionPolicy
  Bypass`, run with a hidden window so no console flashes on each cycle.
- Two Task Scheduler entries named `KnowledgeVault Agent Sync` and
  `KnowledgeVault Agent Sync Logon` are created or updated via `schtasks.exe`:
  one fires every 30 minutes, the other on logon. Both invoke the hidden VBS
  wrapper through `wscript.exe`.
- If the logon trigger can't be registered (`schtasks.exe` failure), a copy
  of the same hidden VBS wrapper is placed in your Startup folder
  (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\KnowledgeVault
  Agent Sync.vbs`) as a fallback so the recurring guard still runs after you
  log in.

The remote policy itself is private data, not an engine runtime derivative. In
a MULTI vault it may be declared at
`03-INFRA/agent-universal-layer/sync/remotes.yaml`, starting from the public
`remotes.yaml.example`. See `docs/sync-contract.md`.

## `agent-doctor --strict`: real live CLI sessions, real network calls

`agent-sync apply` (MULTI profile, Step 6 of `INIT.md`) always ends with a
strict readiness check, `agent-doctor --strict`, whether or not you passed
`--require-ready`. Most of that check only reads local config files, but two
of its probes are live: they start a real session of an installed consumer
CLI and let it talk to whatever backend it is configured to reach, instead
of just checking that a config file looks right.

- **Antigravity (`agy`)**: if `agy` is anywhere on your `PATH` -- whether or
  not you actually chose Antigravity as one of your CLIs -- and the manifest
  expects any MCP servers mounted on it, the doctor runs
  `agy --print "..." --model "Gemini 3.5 Flash (Medium)" --sandbox` (up to
  twice, plus one further targeted prompt per still-unconfirmed server).
  This is a real Antigravity session against its configured model backend:
  it spends your own quota for that model, it is not a local check.
- **OpenCode**: if `opencode` is on your `PATH` (or at
  `~/.opencode/bin/opencode`) and the manifest expects any MCP servers
  mounted on it, the doctor runs `opencode mcp list` for real. If any of the
  MCP servers you have mounted are reached over the network (a Cloud-Server
  `vault-library`, `firecrawl`, `n8n-mcp`, or similar), this call reaches
  them too.

Both probes fire because the binary is present on your `PATH`, not because
you told this profile you wanted that CLI -- so a CLI you installed for some
unrelated reason can still get a live session started on its behalf during
your first `apply`.

Set `NEXGEN_SKIP_LIVE_CONSUMER_PROBES=1` in the environment before running
`agent-sync apply` (or `agent-doctor --strict` directly) to skip both. A
skip is reported as a `warn` naming exactly which probe was skipped and why
-- it is never folded into a silent pass, and it does not block `BASE` or
`PARTIAL` readiness; it only means those two consumers were not live-verified
this run.

## `99-SECRETS/`

Local only. `agent-sync`/agents may write to `99-SECRETS/archive/master-secrets.md.gpg` (GPG-encrypted, git-ignored) and `99-SECRETS/secrets-registry.md` (names and env vars only, never values, tracked in git). See `99-SECRETS/README.md` for the workflow.

## What this never does

No sudo, no changes outside your home directory, no telemetry, and no push to a git remote unless you or an agent explicitly runs a publish step. Every network call this engine makes reaches a service you already set up yourself: Cloud-Server mode only reaches the VPS you point it at over the SSH tunnel you configured, and the one other case -- the live `agent-doctor --strict` probes described above, which can reach Antigravity's model backend or a remote MCP server -- is documented in the section right above this one, together with the `NEXGEN_SKIP_LIVE_CONSUMER_PROBES=1` opt-out.
