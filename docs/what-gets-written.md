# What gets written

This lists every file `install.sh`/`install.ps1`, `INIT.md`, and the MULTI-profile engine (`nexgen`, formerly `agent-sync`/`agent-doctor`; the old names still work as launcher aliases) can create or modify outside this repo. Nothing here touches files outside your home directory, and nothing runs with elevated privileges.

## `install.sh` / `install.ps1`

Both are thin platform shells that find Python 3.11+ and hand off to `03-INFRA/scripts/nexgen_core/bootstrap.py`, which does the actual work identically on every OS.

`install.sh --check` (`install.ps1 -Check` on Windows) is strictly read-only: a missing scaffold folder is reported as a failed check, never created, and nothing is written anywhere.

Plain `install.sh` (the default, guided mode) writes two things:

- Missing scaffold folders inside the repo itself (`01-NOTES/`, `02-PROJECTS/`, `04-NOW/`, `99-INDEX/`, `99-SECRETS/`, each with a `.gitkeep`), and only if they're missing from your clone — which normally does not happen on a full clone.
- The command launchers — `nexgen` and every legacy alias name (`agent-sync`, `agent-doctor`, `vault-push`, and the rest) — into `~/.local/bin`. This is the one thing this script writes outside the repo: small wrapper scripts that just locate Python and forward to this clone's `nexgen_core`. It is the same launcher set the guard cycle (below) keeps aligned on every later run.

The four-question guided interview at the end (CLI count, machine count, where services live) is recommendation-only: nothing you answer is persisted by this script. Nothing here needs admin/root privileges.

## `INIT.md` (the AI-guided installer, any profile)

- `99-INDEX/USER-PROFILE.md`: your profile, hardware, CLI/machine list, and architecture choice.
- Optionally `04-NOW/current-focus.md` or a note under `01-NOTES/`/`02-PROJECTS/`, if you choose to have it ingest a document (CV, project brief, brand rules) during setup.
- `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` **in your own data root**, if it does not exist at all: INIT.md has the assistant create it by copying the shipped `skills.manifest.yaml.example` from the same folder, so the starter command skills (`/vault-doctor`, `/vault-close`, `/vault-save`, `/vault-council`, `/vault-groom`, `/nexgen-update`/`/vault-update`, `/vault-map`) exist after setup instead of only in the README. This is a step the installer performs by hand, in either profile — the guard/apply cycle described below does **not** seed this file on its own; if it is missing, it stays missing until something copies it in. An existing manifest is never rewritten by anything, so emptying it to `skills: {}` is a permanent opt-out.

## Per-CLI bootstrap and MCP config (MINIMAL: done by the agent by hand; MULTI: done by `nexgen sync`/`nexgen guard`)

| CLI | Bootstrap file | MCP config | Skills folder |
|---|---|---|---|
| Claude Code | `~/CLAUDE.md` (pointer to this repo's `AGENTS.md`) | `mcpServers` field in `~/.claude.json` | `~/.claude/skills/`, per skill with `claude` in `targets` |
| Codex | `~/.codex/AGENTS.md` | `~/.codex/config.toml` | `~/.codex/skills/`, per skill with `codex` in `targets` |
| OpenCode | `instructions` array in `opencode.jsonc`/`opencode.json` | `mcp` section of the same file | `~/.opencode/skills/` (plus the shared `~/.agents/skills/` catalog), per skill with `opencode` in `targets` |
| Antigravity | `~/.gemini/config/AGENTS.md` | `~/.gemini/antigravity/mcp_config.json`\* | three folders at once — see below |

A skill only gets a native per-CLI view when its manifest entry declares `exposure: eager` or `exposure: core`; a `lazy` (the default) or `manual` skill stays discoverable only through the shared library and is never copied into a CLI-specific folder, for any of the four CLIs equally — there is no CLI-specific exception to that gate.

Antigravity is the one CLI whose skill views fan out: a skill targeting `antigravity` is written into all three of `~/.gemini/antigravity-cli/skills/`, `~/.gemini/config/skills/`, and `~/.gemini/skills/` (the legacy root), because different ways of launching Antigravity have been observed reading different folders.

MCP sections are additive by default. A server is removed from generated CLI
configs only when its exact old name is deliberately added to the canonical
manifest's `retired_servers` list. Authenticated Antigravity HTTP entries use
the engine-owned `mcp-http-bridge.mjs`; generated JSON stores only the bearer
environment-variable name, never its value.

\* `~/.gemini/antigravity/mcp_config.json` is only the canonical source the
renderer generates. The same rendering step fans it out (via symlink, or a
real file copy on Windows when the symlink/junction privilege is unavailable)
to three further paths: `~/.gemini/antigravity-cli/mcp_config.json`,
`~/.gemini/antigravity-ide/mcp_config.json`, and `~/.gemini/config/mcp_config.json`.
`nexgen doctor` reads back `~/.gemini/antigravity-ide/mcp_config.json` specifically
when it checks that Antigravity's own rendered config carries every server the
manifest expects.

These are patches to files that must already exist (each CLI creates its own default config the first time you open it). Nothing here creates a CLI's config file from scratch; if a chosen CLI has never been opened, that step is skipped for it.

## MULTI profile only, additional writes by the guard cycle (`nexgen sync`/`nexgen guard`, aliases `agent-sync apply`/`agent-sync guard`)

- `~/.config/systemd/user/agent-sync.service` and `agent-sync.timer`, plus `agent-heartbeat.service`/`agent-heartbeat.timer` and `agent-alert@.service`: recurring user-level timers, only on Linux/systemd. `agent-sync.timer` runs `agent-sync guard` (pull + regenerate CLI runtime files + healthcheck, no push) roughly every 30 minutes; `agent-heartbeat.timer` runs an hourly liveness/dependency-watch/self-upgrade pass; `agent-alert@.service` fires only `OnFailure` of the guard unit, to raise an alert when the recurring guard itself stops running. The unit names keep their historical `agent-sync`/`agent-heartbeat`/`agent-alert` spelling even though the command they invoke is `nexgen` under the hood — renaming a systemd unit on an already-installed machine is its own migration, not a side effect of a rename upstream.
- Before overwriting the content of a file it merges into — a CLI's instruction pointer, its permissions/posture file, or a guardrail hook registration (see below) — this cycle copies the previous version alongside it first, with a `.pre-instructions-<timestamp>.bak` suffix for instruction-pointer merges or a `.pre-permissions-<timestamp>.bak` suffix for permission/guardrail merges, in the same folder. These are **not** rotated: one file is kept per change, and nothing deletes the older ones, so they accumulate over time and are yours to clean up. (Rotation — newest three kept — applies separately to the `<file>.bak-<timestamp>` copies the MCP-config renderer writes for `~/.claude.json`, the Antigravity MCP config, the OpenCode config, and `~/.codex/config.toml`.) A third, separately-named convention applies only to skill library/view folders: if a real folder (not a symlink) already occupies a skill's slot with different content — your own hand-made folder, or a leftover from an older layout — it is renamed aside to `<name>.bak-<timestamp>` before the managed symlink takes its place, on any OS, never silently overwritten.
- `~/.local/bin/nexgen-update` on Linux and macOS, or `~/.local/bin/nexgen-update.cmd` on Windows: the real updater command (both are thin launchers generated the same way as every other command name — see the `install.sh` section above — not a separate PowerShell script). It discovers data through `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`, or the normal `~/KnowledgeVault` checkout, then fetches public release tags and does not move the installed branch until it has shown the plan and received confirmation. In a split topology with an existing `99-INDEX/ENGINE-PIN.txt`, the confirmed transaction updates and commits only that pin through `vault-push` before provisioning.
- `~/.nexgen-engine/`: the machine-local state directory (overridable via `AGENT_STATE_DIR`), holding files that never sync anywhere:
  - `agent-sync.lock`: the host-wide transaction lock; it also carries the current holder's PID, command name, and timestamp as plain text, for diagnosing a stuck lock.
  - `agent-guard-liveness`: a timestamp the guard cycle updates on every successful run; the hourly heartbeat alerts if it goes stale.
  - `agent-healthcheck.state`: the alert-debounce ledger (JSON) behind every notification this engine sends, so the same failure doesn't page you every cycle.
  - `third-party-upgrades.md`: a report of pinned third-party dependencies (GitHub-pinned skills, npm-pinned MCP servers) that have moved upstream, written by the hourly heartbeat only, applying nothing.
- `~/.claude/settings.json`: Claude's own settings file, patched additively, and only if — and only where — your private vault declares `agent-universal-layer/permissions/manifest.yaml`; without that file this whole step is a no-op, which is the default for every installation. When it exists, a `posture` entry for `claude` writes `permissions.defaultMode` (and, for the `bypass` posture, `skipDangerousModePermissionPrompt`). A `hooks` entry registers the declared guardrail body as a `PreToolUse` hook matching `Bash`, and copies the body file itself to `~/.claude/<its own declared filename>` (whatever name the private manifest gives it — there is no fixed filename here).
- **OpenCode and Antigravity guardrail hooks** (same manifest, same step — a guardrail hook can target `opencode`/`antigravity` in addition to, or instead of, `claude`): each CLI's native hook contract differs from Claude's, so a thin, engine-owned adapter translates it to/from the same stdin/stdout JSON shape the guardrail body already speaks — the guardrail body itself stays the same private vault file for every CLI it targets.
  - OpenCode: the adapter `opencode-guardrail-plugin.mjs` is copied to `<opencode config dir>/opencode-guardrail-plugin.mjs` and registered (as a `file://` URI, appended, never replacing existing entries) in that config's own `"plugin"` array. The declared guardrail body is copied to `<opencode config dir>/nexgen-guardrail-hooks/`, and a sidecar `<opencode config dir>/nexgen-guardrail.config.json` lists it for the plugin to read.
  - Antigravity: the adapter `antigravity-guardrail-adapter.mjs` is copied to `~/.gemini/config/antigravity-guardrail-adapter.mjs` and registered as a `PreToolUse` entry (key `nexgen-guardrail`) in `~/.gemini/config/hooks.json` — every other top-level hook name already in that file, yours or another tool's, is left untouched. The declared guardrail body is copied to `~/.gemini/config/nexgen-guardrail-hooks/`, with the same kind of sidecar `nexgen-guardrail.config.json` alongside the adapter.
  - Codex has no wired guardrail mechanism at all right now: its own hook contract gates every hook behind a persisted, per-hash trust prompt (or an explicit `--dangerously-bypass-hook-trust` flag) that this engine cannot satisfy unattended. A guardrail declared for `codex` in the manifest is currently a silent no-op — nothing is written, nothing errors — rather than an installed-but-inert hook.
- Windows only, and only if your private vault ships `03-INFRA/scripts/local-model-agent.ps1` (a bring-your-own adapter, never part of the public engine): that script is relinked (or copied, if symlink privileges are unavailable) to `~/.local/bin/local-model-agent.ps1`, alongside two stable wrapper scripts, `~/.local/bin/local-worker.ps1` and `~/.local/bin/local-agent.ps1`.

### Chrome: deliberately not touched

An earlier design considered writing a shadow `google-chrome.desktop` launcher on Linux (and rewriting Chrome's own web-app `.desktop` files) so the shared debug-CDP Chrome always won the "who opens Chrome first" race. That was judged too invasive — silently redirecting a user's existing browser launchers — and was never shipped. `nexgen tool chrome` (alias `agent-chrome`) only starts or reconnects to a Chrome/Chromium process on the local debug port; it writes nothing under `~/.local/share/applications` and never touches any `.desktop` file.

### Windows: scheduled task and hidden wrapper

On Windows, every `nexgen sync`/`nexgen guard` run (`install_scheduler` in
`nexgen_core/scheduler.py`) self-heals the same recurring triggers that systemd provides
on Linux, using only your own user account (no admin elevation, no service):

- A hidden VBS wrapper, `start-agent-sync-hidden.vbs`, and a second one for the
  heartbeat, `start-agent-heartbeat-hidden.vbs`, are written under
  `~/.local/state/` (this path is hardcoded for the Windows scheduler and is
  separate from the `~/.nexgen-engine/` state directory described above). Each
  preserves the resolved engine, vault-data, vault, and branch values, then
  shells out to `agent-sync.ps1 guard` (or `heartbeat`) via `powershell.exe
  -NoProfile -ExecutionPolicy Bypass`, run with a hidden window so no console
  flashes on each cycle.
- Task Scheduler entries named `KnowledgeVault Agent Sync` (every 30 minutes),
  `KnowledgeVault Agent Sync Logon` (on logon), and `KnowledgeVault Agent
  Heartbeat` (hourly) are created or updated via `schtasks.exe`. All three
  invoke their hidden VBS wrapper through `wscript.exe`.
- If the logon trigger can't be registered (`schtasks.exe` failure), a copy
  of the sync wrapper is placed in your Startup folder
  (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\KnowledgeVault
  Agent Sync.vbs`) as a fallback so the recurring guard still runs after you
  log in.

The remote policy itself is private data, not an engine runtime derivative. In
a MULTI vault it may be declared at
`03-INFRA/agent-universal-layer/sync/remotes.yaml`, starting from the public
`remotes.yaml.example`. See `docs/sync-contract.md`.

## `nexgen doctor`: what it checks, and the one real network call

`nexgen doctor` (alias `agent-doctor`) runs entirely read-only checks against local files by default: Git alignment, MCP manifest validity, whether each CLI's rendered config actually carries every server the manifest expects, skill materialization, instruction-pointer alignment, bootstrap-file hygiene, and whether any secret-shaped values have leaked into tracked files. `--fix` (`--remedy`) applies the automatic remedies some of those checks carry; `--strict` only changes how an *undetermined* result (a check that couldn't reach a conclusion, e.g. a CLI that has never been launched on this machine) affects the exit code — it does not turn on any additional check.

The one check that reaches the network is the MCP reachability probe: for every connector the manifest marks `tier: core` (illustratively, the self-hosted Firecrawl, Playwright, `vault-library`, and vault-OCR lanes) whose `require_env` precondition is satisfied, it opens a raw TCP connection to that connector's host and port, with a short per-connector timeout, and reports whether something is listening. This is a plain socket connect, not a CLI session: it starts no `agy`/`opencode`/`claude`/`codex` process, spends no model quota, and runs on every `nexgen doctor` invocation regardless of `--strict`. There is currently no environment variable to skip it.

## `99-SECRETS/`

Local only. Agents, following the workflow in `99-SECRETS/README.md`, may write `99-SECRETS/secrets-registry.md` (names and env vars only, never values, tracked in git). Real credential values are never written by the engine: they stay in per-machine keyrings/env stores, outside the vault. Nothing in the engine itself (the guard cycle, the doctor, the renderer) writes the registry either — it is populated by an agent or by you, by hand, when a secret is added.

## What this never does

No sudo, no changes outside your home directory, no telemetry, and no push to a git remote unless you or an agent explicitly runs a publish step. Every network call this engine makes reaches a service you already set up yourself: Cloud-Server mode only reaches the VPS you point it at over the SSH tunnel you configured, and the one other case — the `nexgen doctor` TCP reachability probe described above, which can reach a self-hosted MCP connector's host — is documented in the section right above this one.
