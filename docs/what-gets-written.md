# What gets written

🇮🇹 [Italiano](it/what-gets-written.md)

Every file `install.sh`, `install.ps1`, `INIT.md` and the MULTI-profile engine (`nexgen`, formerly `agent-sync` and
`agent-doctor`, whose old names still work as launcher aliases) can create or modify outside this repo. Nothing here
touches files outside your home directory, and nothing runs with elevated privileges.

## `install.sh` / `install.ps1`

Both are thin platform shells that find Python 3.11+ and hand off to `03-INFRA/scripts/nexgen_core/bootstrap.py`,
which does the work identically on every OS.

`install.sh --check` (`install.ps1 -Check` on Windows) is strictly read-only: a missing scaffold folder is reported as
a failed check and never created.

Plain `install.sh`, the default guided mode, writes two things:

- Missing scaffold folders inside the repo itself (`01-NOTES/`, `02-PROJECTS/`, `04-NOW/`, `99-INDEX/`, `99-SECRETS/`,
  each with a `.gitkeep`), and only if your clone lacks them, which normally does not happen on a full clone.
- The command launchers, `nexgen` plus every legacy alias (`agent-sync`, `agent-doctor`, `vault-push`, and the rest),
  into `~/.local/bin`. This is the one thing the script writes outside the repo: small wrappers that locate Python and
  forward to this clone's `nexgen_core`. The guard cycle keeps the same launcher set aligned on later runs.

The four-question guided interview at the end (CLI count, machine count, where services live) only recommends; the
script persists none of your answers. Nothing here needs admin or root.

## `INIT.md` (the AI-guided installer, any profile)

- `99-INDEX/USER-PROFILE.md`: your profile, hardware, CLI and machine list, and architecture choice.
- Optionally `04-NOW/current-focus.md` or a note under `01-NOTES/` or `02-PROJECTS/`, if you have it ingest a document
  (CV, project brief, brand rules) during setup.
- `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` in your own data root, if it does not exist at all.
  INIT.md has the assistant create it by copying the shipped `skills.manifest.yaml.example` from the same folder, so
  the starter commands exist after setup instead of only in the README. The installer does this by hand in either
  profile: the guard and apply cycle does not seed this file, so a missing manifest stays missing until something
  copies it in. Nothing ever rewrites an existing manifest, so emptying it to `skills: {}` is a permanent opt-out.

## Per-CLI bootstrap and MCP config (MINIMAL: the agent writes by hand; MULTI: `nexgen sync` and `nexgen guard`)

| CLI | Bootstrap file | MCP config | Skills folder |
|---|---|---|---|
| Claude Code | `~/CLAUDE.md` (pointer to this repo's `AGENTS.md`) | `mcpServers` field in `~/.claude.json` | `~/.claude/skills/`, per skill with `claude` in `targets` |
| Codex | `~/.codex/AGENTS.md` | `~/.codex/config.toml` | `~/.codex/skills/`, per skill with `codex` in `targets` |
| OpenCode v2 | `~/.config/opencode/AGENTS.md`, a symlink to this repo's `AGENTS.md` (V2 accepts the `instructions` array but never resolves it, so it is no longer written; engine-added entries are removed once, user entries stay) | `mcp.servers` in `opencode.jsonc` or `opencode.json`, with the flat V1 layout migrated once, plus the native `plugins` and `permissions` keys (`plugin` and `permission` migrate once, user rules are never overridden) | `~/.config/opencode/skills/` native views and the shared `~/.agents/skills/` catalog, per skill with `opencode` in `targets` |
| Antigravity | `~/.gemini/config/AGENTS.md` | `~/.gemini/antigravity/mcp_config.json`\* | three folders at once, see below |
| OpenClaw | the managed block in each agent workspace's `AGENTS.md` (`agents.entries.<id>.workspace`, else `agents.defaults.workspace`, else `<state dir>/workspace`); OpenClaw has no pointer file of its own | `mcp.servers` in `~/.openclaw/openclaw.json`, or the file `OPENCLAW_CONFIG_PATH` names, or `OPENCLAW_STATE_DIR/openclaw.json` | the shared `~/.agents/skills/` catalog, which OpenClaw discovers itself, per skill with `openclaw` in `targets` |

`exposure` does not decide whether a skill gets a view. A declared skill is linked into every runtime named in its
`targets`, whatever its exposure; `lazy`, the default, `eager`, `manual` and `core` only change how the skill is
presented and loaded, through `INDEX.md` and on-demand reads.

Antigravity is the one CLI whose skill views fan out: a skill targeting `antigravity` is written into all three of
`~/.gemini/antigravity-cli/skills/`, `~/.gemini/config/skills/` and `~/.gemini/skills/`, the legacy root, because
different ways of launching Antigravity have been observed reading different folders.

MCP sections are additive by default: a server already in a CLI's config but not in the manifest is preserved,
because it may be yours. Two kinds of name leave every generated CLI config anyway: an exact name the manifest lists
under `retired_servers`, an explicit tombstone, and a name this engine declared at an earlier sync that the manifest no
longer declares. The engine remembers the names it declared at its last real render in the machine-local
`mcp-state.json`, so a removal reaches every CLI even when nobody writes a tombstone. A name the engine never declared
is never touched, and a running trial or an `orphans_allowlist` entry keeps its name alive. `nexgen doctor` reports a
stale entry of the second kind as engine debris that the next `nexgen sync` removes, never as something to adopt.
Authenticated Antigravity HTTP entries use the engine-owned `mcp-http-bridge.mjs`; the generated JSON stores only the
bearer environment variable name, never its value.

\* `~/.gemini/antigravity/mcp_config.json` is the canonical file the renderer generates. The same step fans it out, by
symlink or by a real copy on Windows when the symlink privilege is unavailable, to three further paths:
`~/.gemini/antigravity-cli/mcp_config.json`, `~/.gemini/antigravity-ide/mcp_config.json` and
`~/.gemini/config/mcp_config.json`. `nexgen doctor` reads back `~/.gemini/antigravity-ide/mcp_config.json`
specifically when it checks that Antigravity's rendered config carries every server the manifest expects.

These are patches to files that must already exist, because each CLI creates its own default config the first time you
open it. Nothing here creates a CLI's config from scratch; if you never opened a chosen CLI, that step is skipped for
it.

### OpenClaw: three rules its schema does not let you guess

OpenClaw is the harness with the interface (web panel, phone app) through which the other harnesses are used, so its
file is its own: the engine writes only the `mcp.servers` table and leaves models, channels and gateway settings alone.
Three rules of its schema silently cost you a connector, so the renderer enforces them:

- The URL is validated before `${VAR}` is substituted. A `${VAR}` left in the `url` field is refused, so the URL is
  written already resolved from the variable the manifest names. If that variable is unset, the server is skipped the
  way `require_env` skips one.
- An http server needs `"transport": "streamable-http"`. Without it the handshake times out and nothing explains why.
- A Python MCP server dies with `UnicodeEncodeError` on Windows as soon as a value carries an accent or an emoji.
  Every server whose command is a Python interpreter (`python`, `python3`, `py`, or a virtualenv's `.exe`) receives
  `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` in its environment, on every platform.

Timeouts are converted from the manifest's seconds to `connectionTimeoutMs` and `requestTimeoutMs`, and a `tools_deny`
list becomes `"toolFilter": {"exclude": [...]}`. Secrets stay references (`"Authorization": "Bearer ${NAME}`), never
values.

## MULTI profile only: additional writes by the guard cycle (`nexgen sync`, `nexgen guard`, aliases `agent-sync apply`, `agent-sync guard`)

- `~/.config/systemd/user/agent-sync.service` and `agent-sync.timer`, plus `agent-heartbeat.service`,
  `agent-heartbeat.timer` and `agent-alert@.service`: recurring user-level timers, only on Linux with systemd.
  `agent-sync.timer` runs `agent-sync guard` (pull, regenerate the CLI runtime files, healthcheck, no push) roughly
  every 30 minutes. `agent-heartbeat.timer` runs an hourly liveness, dependency-watch and self-upgrade pass.
  `agent-alert@.service` fires only on `OnFailure` of the guard unit, to raise an alert when the recurring guard itself
  stops running. The unit names keep their historical `agent-sync`, `agent-heartbeat` and `agent-alert` spelling even
  though the command underneath is `nexgen`: renaming a systemd unit on an installed machine is its own migration.
- Before overwriting the content of a file it merges into (a CLI's instruction pointer, its permissions file, or a
  guardrail hook registration), the cycle copies the previous version alongside, with a
  `.pre-instructions-<timestamp>.bak` suffix for instruction-pointer merges and a `.pre-permissions-<timestamp>.bak`
  suffix for permission and guardrail merges. Instruction-pointer backups are not rotated and accumulate; permission
  backups keep the newest three, as do the `<file>.bak-<timestamp>` copies the MCP renderer writes for
  `~/.claude.json`, the Antigravity MCP config, the OpenCode config and `~/.codex/config.toml`. A third convention
  applies only to skill library and view folders: if a real folder, not a symlink, already occupies a skill's slot
  with different content, your own hand-made folder or a leftover from an older layout, it is renamed aside to
  `<name>.bak-<timestamp>` before the managed symlink takes its place, on any OS, never silently overwritten.
- `~/.local/bin/nexgen-update` on Linux and macOS, or `~/.local/bin/nexgen-update.cmd` on Windows: the real updater
  command, a thin launcher generated like every other command name. It discovers data through `AGENT_VAULT_DATA` or
  `KNOWLEDGE_VAULT_PATH`, or the normal `~/KnowledgeVault` checkout, then fetches public release tags and does not move
  the installed branch until it has shown the plan and received confirmation. In a split topology with an existing
  `99-INDEX/ENGINE-PIN.txt`, the confirmed transaction updates and commits only that pin through `vault-push` before
  provisioning.
- `~/.nexgen-engine/`: the machine-local state directory, overridable with `AGENT_STATE_DIR`, holding files that never
  sync anywhere:
  - `agent-sync.lock`: the host-wide transaction lock, carrying the current holder's PID, command name and timestamp
    as plain text, for diagnosing a stuck lock.
  - `agent-guard-liveness`: a timestamp the guard updates on every successful run; the hourly heartbeat alerts if it
    goes stale.
  - `agent-healthcheck.state`: the alert-debounce ledger (JSON) behind every notification, so the same failure does not
    page you every cycle.
  - `third-party-upgrades.md`: a report of pinned third-party dependencies (GitHub-pinned skills, npm-pinned MCP
    servers) that moved upstream or lost publisher support, plus the third-party components a switched-on module
    declares it carries (`upstream:` in the module catalog). The hourly heartbeat writes it and applies nothing.
- `~/.claude/settings.json`, Claude's own settings file, patched additively, and only if your private vault declares
  `agent-universal-layer/permissions/manifest.yaml`; without that file the whole step is a no-op, which is the default.
  When it exists, a `posture` entry for `claude` writes `permissions.defaultMode` and, for the `bypass` posture,
  `skipDangerousModePermissionPrompt`. A `hooks` entry registers the engine's `claude-guardrail-adapter.mjs` (copied to
  `~/.claude/` with the shared `nexgen-guardrail-core.mjs`) as a `PreToolUse` hook matching `Bash`, and copies the
  declared guardrail body to `~/.claude/nexgen-guardrail-hooks/<its own declared filename>`, whatever name the private
  manifest gives it, with a sidecar `~/.claude/nexgen-guardrail.config.json` naming it. The adapter exists because a
  body registered directly fails open: Claude treats any exit code but 2, and any timeout, as a non-blocking error and
  runs the command. The adapter runs the body in a subprocess, turns every abnormal outcome (crash, hang, missing file,
  unreadable answer) into an explicit decision (`ask`, or `deny` under `bypassPermissions`), and exits 2 if it fails
  itself. A registration written by an earlier version, where the body itself was the hook, is migrated in place.
- OpenCode, Antigravity and OpenClaw guardrail hooks, from the same manifest and the same step. A guardrail hook can
  target `opencode`, `antigravity` or `openclaw` in addition to, or instead of, `claude`. Each CLI's native hook
  contract differs from Claude's, so a thin engine-owned adapter translates it to and from the stdin and stdout JSON
  shape the guardrail body already speaks; the body stays the same private vault file for every CLI it targets. All
  four adapters share one core (`nexgen-guardrail-core.mjs`, deployed beside each) and record every consultation in
  `~/.local/state/guardrail/<cli>.json`, a count and a time, never the command. That is what `nexgen doctor` reads to
  tell a guardrail the CLI really calls from one that is only registered.
  - OpenCode: the guardrail is a plugin directory, `<opencode config dir>/plugins/nexgen-guardrail/`, which OpenCode
    loads by itself; nothing about it is written into `opencode.json` or `opencode.jsonc`. The directory holds the
    adapter as `index.mjs` (the V2 shape `{ id, setup(ctx) }`), `nexgen-guardrail-core.mjs`, the declared body under
    `hooks/`, and a sidecar `nexgen-guardrail.config.json`. The plugin vetoes a denied command at
    `ctx.shell.hook("create.before")`, whatever the permission rules say, and answers the permission request at
    `ctx.permission.hook("evaluate")`. Earlier engines registered the adapter file in the `plugins` array; OpenCode
    2.0.24 refuses that ("configured plugin path must be a directory") and no longer calls the V1 `permission.ask`
    hook, so that guardrail was never consulted. The first run of the new version takes back that registration and the
    files it deployed, by name, and leaves other plugins alone. Posture is ordered V2 `permissions` rules (`edit` and
    `shell` allow or ask); the legacy `permission` object migrates once into equivalent rules, and an explicit user
    rule on the same pair always wins. With the plugin present, `bypass` renders as `shell: ask` and the plugin answers
    `allow` for what the body permits (so there is still no prompt) and `deny` for the rest; the sidecar records this
    as `autoAllow`. If OpenCode did not call the plugin, every shell command would prompt visibly instead of going
    unchecked silently. The OpenCode event sink is no longer installed, and any legacy registration is cleaned up on
    sync.
  - Antigravity: `antigravity-guardrail-adapter.mjs` is copied to `~/.gemini/config/antigravity-guardrail-adapter.mjs`
    and registered as a `PreToolUse` entry under the key `nexgen-guardrail` in `~/.gemini/config/hooks.json`. Every
    other top-level hook name in that file, yours or another tool's, is left untouched. The declared body goes to
    `~/.gemini/config/nexgen-guardrail-hooks/`, with the same kind of sidecar beside the adapter.
  - OpenClaw: a native plugin directory, `<openclaw state dir>/extensions/nexgen-guardrail/`, holding the adapter
    (`index.mjs`), `package.json`, `openclaw.plugin.json`, the shared core, the declared body under `hooks/` and the
    sidecar. The plugin answers `before_tool_call` for the tools that carry a shell command (`exec`, `process`,
    `terminal`, `code_execution`): a denial blocks the call, a question becomes an approval request. `openclaw.json` is
    never touched by this step, because editing `plugins.*` under a running gateway breaks it, so enabling the plugin
    is `nexgen openclaw setup` with the gateway stopped, and `nexgen doctor` reports "deposited but not enabled" until
    then. The hook covers only the calls OpenClaw executes itself; an agent on the `claude-cli` or `codex` runtime runs
    its tools inside that CLI, where its own guardrail already applies.
  - Codex has no wired guardrail mechanism. Its hook contract gates every hook behind a persisted per-hash trust
    prompt, or an explicit `--dangerously-bypass-hook-trust` flag, that this engine cannot satisfy unattended. A
    guardrail declared for `codex` is a silent no-op: nothing is written and nothing errors.
- Windows only, and only if your private vault ships `03-INFRA/scripts/local-model-agent.ps1` (a bring-your-own
  adapter, never part of the public engine): that script is relinked, or copied when symlink privileges are
  unavailable, to `~/.local/bin/local-model-agent.ps1`, alongside two stable wrappers,
  `~/.local/bin/local-worker.ps1` and `~/.local/bin/local-agent.ps1`.
- Legacy event-sink cleanup. Older engines registered `nexgen-event-sink.mjs` on each CLI's lifecycle events; the engine
  now strips those registrations and removes the obsolete file across all harnesses.
- Update notice in your shell and at login, with an off switch. The guard keeps a guarded block in `~/.bashrc`, plus
  `~/.zshrc`, fish and PowerShell profiles where those shells exist here (a PowerShell profile is written only where
  `pwsh` is installed or the profile already exists), and a boot-time check: a systemd user timer and XDG autostart on
  Linux, a logon task on Windows. `nexgen tool update-notifier --install-shell-hook --remove` and
  `--install-autostart --remove` remove them and record the choice in `update-notifier-optout.txt` in the
  machine-local state directory, so the next guard cycle does not put them back; the matching install command lifts
  it.
- Antigravity's four config paths. `~/.gemini/antigravity/mcp_config.json` is the canonical file;
  `~/.gemini/{antigravity-cli,antigravity-ide,config}/mcp_config.json` are links to it, copies where links cannot be
  made. A real file already at one of those three paths is backed up beside itself first, and any servers it holds
  that the canonical file lacks are carried into it.
- The Playwright wrapper patches npm's own cache. `playwright-human-safe.mjs` runs `@playwright/mcp` at the exact
  version it pins and applies its human-safety patches to the copy in npm's `_npx` cache, keeping the original beside
  it as `*.agent-human-file-chooser-patch-v1.original`. That copy is shared with anyone else running
  `npx @playwright/mcp@<same version>` on this machine; `node playwright-human-safe.mjs --restore` puts the original
  back. There are five patches, each tied to a defect that shows on a Chrome a person shares with the agent: upstream
  grabs every native file chooser, resets the browser's download behavior while attached, never brings a new tab to the
  front (so pointer actions on it hang on a headed browser), closes the context it was handed when the last client
  leaves, and adopts an installed web-app window as a spare tab. The wrapper also passes `--no-webmcp`, so a page
  cannot register tools for the agent. `node playwright-human-safe.mjs --self-test` checks that every patch still
  applies to the pinned version; a version it cannot patch is refused rather than half-patched.

### Chrome: deliberately not touched

An earlier design considered writing a shadow `google-chrome.desktop` launcher on Linux, and rewriting Chrome's own
web-app `.desktop` files, so the shared debug-CDP Chrome always won the race for who opens Chrome first. That was too
invasive, silently redirecting a user's existing browser launchers, and never shipped. `nexgen tool chrome` (alias
`agent-chrome`) only starts or reconnects to a Chrome or Chromium process on the local debug port; it writes nothing
under `~/.local/share/applications` and never touches a `.desktop` file.

### Windows: scheduled task and hidden wrapper

On Windows, every `nexgen sync` and `nexgen guard` run (`install_scheduler` in `nexgen_core/scheduler.py`) self-heals
the same recurring triggers systemd provides on Linux, using only your own user account, with no admin elevation and no
service:

- Two hidden VBS wrappers, `start-agent-sync-hidden.vbs` and `start-agent-heartbeat-hidden.vbs`, are written under
  `~/.local/state/`. That path is hardcoded for the Windows scheduler and separate from the `~/.nexgen-engine/` state
  directory. Each preserves the resolved engine, vault-data, vault and branch values, then shells out to the installed
  `agent-sync.cmd` shim (`guard` or `heartbeat` mode) through `cmd.exe /c`, with a hidden window so no console flashes
  on each cycle.
- Task Scheduler entries named `KnowledgeVault Agent Sync` (every 30 minutes), `KnowledgeVault Agent Sync Logon` (on
  logon) and `KnowledgeVault Agent Heartbeat` (hourly) are created or updated through `schtasks.exe`. All three invoke
  their VBS wrapper through `wscript.exe`.
- If the logon trigger cannot be registered (`schtasks.exe` failure), a copy of the sync wrapper goes into your Startup
  folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\KnowledgeVault Agent Sync.vbs`) so the recurring
  guard still runs after you log in.

The remote policy is private data, not an engine derivative. In a MULTI vault it may be declared at
`03-INFRA/agent-universal-layer/sync/remotes.yaml`, starting from the public `remotes.yaml.example`. See
[sync-contract.md](sync-contract.md).

## `nexgen doctor`: what it checks, and the one real network call

`nexgen doctor` (alias `agent-doctor`) runs read-only checks against local files by default: Git alignment, MCP
manifest validity, whether each CLI's rendered config carries every server the manifest expects, skill
materialization, instruction-pointer alignment, bootstrap-file hygiene, and whether secret-shaped values leaked into
tracked files. `--fix` (`--remedy`) applies the automatic remedies some checks carry. `--strict` only changes how an
*undetermined* result affects the exit code, where undetermined means a check that could not reach a conclusion, such
as a CLI never launched on this machine; it turns on no additional check.

The one check that reaches the network is the MCP reachability probe. For every connector the manifest marks `tier:
core` (for example Playwright, `vault-library` and the vault-OCR lanes) whose `require_env` precondition is satisfied,
it opens a raw TCP connection to that connector's host and port, with a short per-connector timeout, and reports
whether something is listening. That is a plain socket connect, not a CLI session: it starts no `agy`, `opencode`,
`claude` or `codex` process, spends no model quota, and runs on every invocation regardless of `--strict`. No
environment variable skips it.

A connector with no address on this machine gets the other half of the same question. Firecrawl is the vendor's cloud
API, so its key decides whether it exists here: `nexgen doctor` reports whether `FIRECRAWL_API_KEY` is present, and
reads presence alone, never comparing, printing or writing the value into a report, an alert or a log. A machine
without the key gets a warning naming the store command, not a failure: declining the connector is a choice.

## `99-SECRETS/`

Local only. Agents, following the workflow in `99-SECRETS/README.md`, may write `99-SECRETS/secrets-registry.md`
(names and environment variables only, never values, tracked in git). The engine never writes credential values: they
stay in per-machine keyrings and environment stores, outside the vault. Nothing in the engine writes the registry
either; an agent or you populate it by hand when a secret is added.

## What this never does

No sudo, no changes outside your home directory, no telemetry, and no push to a git remote unless you or an agent
explicitly runs a publish step. Every network call reaches a service you set up yourself: Cloud-Server mode reaches
only the VPS you point it at over the SSH tunnel you configured, and the other case is the doctor's TCP reachability
probe described above.
