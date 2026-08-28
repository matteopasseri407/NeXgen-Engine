# Changelog

All notable changes to the NeXgen engine are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[Semantic Versioning](https://semver.org/).

This file tracks the **engine** (this repo). Your own data — manifests,
instructions, skills, secrets — lives in your KnowledgeVault and is not part
of any engine release.

## [Unreleased]

### Added

- **`nexgen plan`** (and `nexgen sync --dry-run` / `--check`): the plan of
  what an apply would change, computed read-only and network-free. `plan`
  exposes drift at exit 0, `plan --check` exits non-zero on drift for CI,
  `plan --json` dumps the plan with provenance. The plan reuses the doctor's
  read-only checks as drift probes instead of reimplementing them, and
  declares its own limits (no upstream check without a fetch; idempotent
  self-repair phases declared, not enumerated).
- **`nexgen import --from <cli>`**: manifest stubs generated from a live CLI
  config, on stdout by default (read-only), `--apply` to write with backup +
  validation + rollback. Stub entries are semantically validated before they
  are shown or written: unsafe manifest keys, non-http(s) URLs, multi-line
  commands/args/env values and auth forms that are not env references are
  refused on both paths.
- **`nexgen init --local`**: the no-conversation install for a single
  machine. Same code path as the guided installer with the answers already
  decided (profile MINIMAL, services LOCAL-ONLY): profile, `local` remote,
  seeded skill manifest, commands installed, first alignment, final doctor
  verdict. No questions, no secrets, no remote required.
- **MCP tool annotations** surfaced end to end: the lazy proxy carries the
  upstream `readOnlyHint/destructiveHint/idempotentHint/openWorldHint`
  through `lazy_list` (advisory only — the fail-closed confirmation gate
  still depends solely on the manifest's `readonly`/`readonly_tools`), the
  three meta-tools declare their own annotations, and every vault-library
  tool declares read-only or mutating.
- **`nexgen vault map --context <note> --hops N`**: the neighbourhood of one
  note on the wikilink graph (read-only BFS, resolves path/stem/title),
  capped neighbours, `--json` for agents.
- **Orphan-artifact check, WARN-only with allowlist** (`nexgen doctor`): MCP
  servers configured outside the manifest and skills materialized outside
  the manifest are now a WARN carrying the config path — never removed,
  never blocking. Legitimate orphans go into the manifests' own
  `orphans_allowlist` key (`"cli:name"` or bare name for MCP; skill names
  for skills). Out-of-manifest skills moved from UNDETERMINED to WARN so
  they stop appearing as a permanent undetermined state in the human report.

### Fixed

- **vault-library MCP: the read index now excludes `99-SECRETS` by default.**
  The write path already refused it, but the read index (search, read,
  related, map) indexed everything unless `EXCLUDE_PATH_PREFIXES` was set —
  and the first deployment never set it. Secrets recovery through search is
  exactly the path the vault's own rules forbid; the default is now the safe
  one and an explicit env still wins.
- **vault-library MCP: search snippets are redacted and bounded** (~200
  chars, labelled secrets, bearer forms, PEM/age blocks and long
  high-entropy runs masked as `[redacted]`). A snippet is orientation, not
  fidelity.

## [2.1.1] - 2026-08-27

### Fixed

- **Universal Event Sink multi-dialect and reasoning filtering**:
  - Multi-dialect JSONL transcript parsing (`extractFromRow`) supporting Antigravity (`type: "PLANNER_RESPONSE"` / `source: "MODEL"`), Claude, and Codex.
  - Aligned Antigravity `Stop` lifecycle hook structure in `hooks.json` to flat handler array per native Antigravity specifications.
  - OpenCode plugin: implemented turn-end snapshot on `session.idle` with strict whitelist on `type === "text"` parts and local SQLite (`opencode.db`) fallback.
  - Stripped reasoning / internal thinking blocks (`<think>`, `<thought>`, `[thought]`, `<reasoning>`) to prevent leaking chain-of-thought into voice TTS.
  - Unified session ID propagation across all runtimes (`COCKPIT_SESSION_ID || conversationId || session_id || sessionID`).

## [2.1.0] - 2026-08-26

### Added

- **Module hosting.** A repository carrying a `nexgen-module.yaml` at its root
  is a module: a machine picks it up with `nexgen modules add <path>`, and the
  engine installs and repairs what the manifest declares. Adding a module no
  longer means editing the engine.
- Manifest vocabulary for modules the catalog could not describe before:
  `provides` (shims, systemd units, runtime hooks, config files, a compose
  file) and `requires` (binaries, devices, groups, paths, free VRAM, and the
  `setup` command that provisions them). Every field is declarative; none of
  them runs module-supplied code.
- `scope: host | shared`. The state file travels between machines through the
  vault, so a flat map declares the same thing everywhere. A module bound to
  one desktop now says so, and `modules set` writes it under that host by
  itself. External modules default to `host`.
- `health`: a command that proves a module works, not merely that its files are
  present. Run by `nexgen doctor`, never by the guard cycle.
- `nexgen modules add|remove` for external modules, and `--here|--everywhere`
  as an explicit override of a module's declared scope.
- The guard cycle installs, repairs and uninstalls declared modules. A deleted
  shim or a disabled unit is drift like any other; a module declared `absent`
  is actually removed, and only files the engine generated are touched.

### Fixed

- **The event sink emitted the hook's own JSON as the reply.** A Claude Stop
  hook carries `{session_id, transcript_path, cwd, hook_event_name}` and no
  reply text at all, and the sink fell back to the raw buffer. It now reads the
  tail of the transcript and extracts the last assistant prose, skipping
  `isSidechain` rows, which are subagent turns and not the reply. With no prose
  it emits no text rather than something unreadable.
- The sink no longer lets a CLI's internal session id shadow
  `COCKPIT_SESSION_ID`. Consumers gate on the latter, so every event looked as
  if it came from a session nobody had marked vocal, and was dropped in
  silence.
- The sink's end-to-end test waited on `accept()` without a deadline. It did
  not fail, it hung, and took the rest of the suite with it.


## [2.0.5] - 2026-08-25

### Fixed

- Maintenance and bug fixes.

## [2.0.4] - 2026-08-24

### Changed

- **README reach optimization:** New `README.md` with comparison table (NeXgen vs AgentSync/mcp-sync/mcps-manager/dotfiles-ai), demo section (`nexgen info`/`shell`/`doctor`), dual quick-start (`uv tool install` + `git clone`), star badge, platform badges, `<picture>` WebP delivery, FAQ and architecture links; Italian content moved to `README.it.md` for SEO. Fixes `pyproject.toml` homepage (`matteopasseri407/NeXgen-Engine`) and adds `Repository`/`Issues`/`Changelog` URLs plus expanded keywords for GitHub/PyPI discovery.

### Fixed

- **Banner asset weight:** `assets/nexgen-architecture-banner.png` compressed from 986 KB to ~34 KB (palette + `optimize`) and shipped with `assets/nexgen-architecture-banner.webp` (~39 KB) for faster GitHub rendering and mobile LCP. Visual quality preserved via adaptive palette.

## [2.0.3] - 2026-08-23

### Fixed

- Maintenance and bug fixes.

## [2.0.2] - 2026-08-23

### Fixed

- **Canonical Windows Chrome profile resolution**: `get_profile_dir()` in `nexgen_core.tools.chrome` checks for `~/ChromeDebugProfile` before falling back to `AppData/Local/Google/Chrome/User Data/chrome-agent-debug`, maintaining parity with single-profile Windows setups on Chrome 136+.
- **Council unassigned Privacy role handling on non-GPU hosts**: `_parse_governor_role_tables` in `council.routing` recognizes `—` in top candidate slots for the local-only `Privacy` role as explicitly unassigned (`roles["Privacy"] = ()`) instead of raising a fatal `RoutingContractError`, allowing `council propose` to succeed on machines without local GPU models.

## [2.0.1] - 2026-08-23

### Fixed

- **Docker base image for vault-mcp**: bumped to `python:3.13-slim` matching the `requires-python = ">=3.13"` constraint in `pyproject.toml`.
- **PowerShell candidate slicing on Windows**: guarded slicing in `install.ps1` to prevent empty/duplicate argument injection when candidate array length is 1.
- **Council seat validation without local seats.yaml**: `_check_seat_allowed` in `proposal.py` now receives the loaded config and avoids exiting when `seats.yaml` is absent in clean test fixtures.
- **CI unit test CLI mocks**: added missing `shutil.which` mocks in `test_nexgen_council_routing.py` and `test_vault_groom.py`, and cleaned duplicated test definitions.

## [2.0.0] - 2026-08-22

### Added

- **Lazy MCP mounting contract**: a server is mounted only when actually used.
  The manifest declares `tier` (core = always), `enabled`, `lazy` and
  `lazy_targets`; the renderer keeps configs clean by removing servers that
  are no longer mounted. Fail-closed by default: a tool is mutating until an
  allowlist grants read-only (`readonly`/`readonly_tools`), and MCP tool
  annotations are never trusted.
- **The waiter (`lazy-mcp.py`)**: an MCP 2.0 index+load proxy, always mounted
  on every CLI with three meta-tools — `lazy_list` (live index, budgeted),
  `lazy_load` (full schema before the call), `lazy_call` (forward with the
  fail-closed gate, audit log, idle process sweep). It is the native deferral
  of Claude Code, for the CLIs that lack one.
- **Dependency provisioning (`deps:` contract)**: servers and skills declare
  `npx:`/`git:` pinned dependencies that are materialized lazily at first
  spawn (`${DEPS_WORKSPACE}`), checked offline-safe by the doctor, never
  installed eagerly, and never unpinned (`@latest` is refused).
- **Skills: always-lazy routing** through the generated catalog
  (`~/.agents/skills/INDEX.md`), the same `deps:` contract as MCP servers,
  and the new `upstream` origin for third-party skills (inventoried in the
  manifest, never vendorized).
- **Modules**: a canonical module catalog (`modules.yaml`: memory,
  semantic-rag, firecrawl, ocr, n8n, browser, council, sync) with per-machine
  state (`modules.state.yaml`, written only by `nexgen modules set`), an
  agent-driven install step in INIT.md (1.4b) and doctor checks; activating a
  module without its env gates is a reported error, not a silent default.
- **Public rules-vs-identity contract**: `AGENTS.md` holds only operational
  rules; a sanitized `agent-self.md` template ships with status validation
  in the doctor.
- **Council: pay-per-use gate** with interactive confirmation and per-seat
  usage recap; **Antigravity (agy) unblocked as a full seat** (oracle-mode
  flags, effort forwarding) and verified live.
- **`nexgen info`**: visual system status dashboard. **`nexgen shell`**:
  standalone operator shell.
- **Installing without an assistant already installed**, and transitional
  launchers with an observable expiry.
- **OAuth client id for remote MCP servers** (`oauth_client_id` in the
  manifest, public identifier only): the renderer emits it into OpenCode
  config from environment variables.

### Changed

- **The sync is resilient**: poisoned input goes to quarantine, and
  uncommitted work survives realignment instead of being clobbered.
- **The machine you develop on is no longer overwritten** by the sync, and
  the handover from the previous install is survived cleanly.
- **Running a pre-release is not a fault** reported every hour; oversized
  detail notes are WARN, not FAIL.
- **The README platform table is generated** and stops describing a specific
  laptop; add-on repositories are consistently named `NeXgen-addon-*`.
- **The council parses the governor v4 routing block** (channel columns,
  prescelto/rimpiazzo slots) instead of the stale legacy table.

### Fixed

- The waiter: engine-root resolution from the executing checkout, stdio
  hangs, Windows process-group termination, network guards, and the
  fail-closed mutating gate (Kimi review, BLOCKER).
- First end-user bug pass: additive Codex rendering, `--version`, the config
  setter, truthful push output, warning marks, instruction deduplication and
  tool programs.
- A sandboxed cycle could take the real machine's lock; GitHub skills were
  never cloned; errors wore a checkmark tick; line endings counted as
  differences; a config carrying the secret itself is refused.
- Council: `tokens`/`cost` can arrive as dicts and crashed the usage
  summation (found by the kimi seat).

### Security & Robustness

- **Escaped profile paths in `agent-chrome --heal` on Windows**: Sanitized single quotes when constructing PowerShell commands in `heal_chrome` (`nexgen_core/tools/chrome.py`) to prevent command injection or syntax errors when paths or user environments contain apostrophes or spaces.
- **Race-free 0600 file permissions on generated secrets**: Files holding operational secrets and environment variables (`.env`, `90-nexgen-stack.conf`) in `nexgen_core/stack/secrets.py` are now created directly with `0o600` permissions (`_write_restricted_text`), eliminating the race window where they were briefly world-readable under default umasks.
- **Cross-platform and user-scoped lock in `vault-mcp`**: The `vault-library` write lock now uses a user-scoped lock path in the system temporary directory with dual POSIX (`fcntl`) and Windows (`msvcrt`) locking support, preventing multi-user permission collisions and Windows import errors.
- **Safe argument splitting for manifest build commands**: Replaced naive whitespace splitting with `shlex.split` in `_run_build` (`nexgen_core/provision.py`) to correctly parse quoted arguments with spaces.

## [0.99.1] - 2026-08-17

### Fixed

- **Antigravity could not reach an MCP server that enforces protocol revision
  `2026-07-28`.** The bundled `mcp-remote` bridge predates the revision and
  never sends `Mcp-Method`, nor `Mcp-Name` when the body names a target, so a
  server enforcing it answers HTTP 400 to every request -- discovery included.
  The CLI then waits for a tool list that never arrives, which reads as a hang
  rather than the protocol error it is: `agy --print` timed out and the doctor
  reported a bare "behavioral probe timed out". `mcp-remote@0.1.38` is its last
  published version, so there is no upstream fix to wait for. The bridge now
  runs a loopback shim in front of it that derives the missing headers from the
  body it is already forwarding; everything else mcp-remote does (SSE, sessions,
  retries, OAuth discovery) is left alone. The shim binds `127.0.0.1` on an
  ephemeral port, and the bearer stays where it already was -- in the header
  mcp-remote sets, never in an argument and never in a config file.
- **The bridge crashed instead of reporting a child killed by a signal.**
  `process.constants` does not exist in Node, so the exit handler threw
  `TypeError: Cannot read properties of undefined (reading 'signals')` every
  time the child died from a signal. The table comes from `node:os` now.

### Changed

- **The doctor names the MCP server that hung the Antigravity probe**, in both
  twins, instead of only reporting that the probe timed out. A bare timeout says
  the CLI hung but never which server hung it, which is the one fact needed to
  fix it. agy's own log lists the servers it is still waiting on: the probe
  passes `--log-file` and reads only those names out of it. Nothing else from
  the log is echoed.

## [0.99.0] - 2026-08-16

### Added

- **`render.py --dump-config`** (read-only): replays the full configuration
  pipeline, one view per CLI, with every value annotated by the layer that
  produced it (`manifest`, `manifest + live-additive (...)`,
  `live-extra (not in manifest)`). Never writes, never touches the
  merge/write functions -- a pure replay of the same path the writers use
  (`os_view` -> `r_<cli>` -> preserve per-server -> keep-extras). For
  debugging ("why is my config different from expected?"), onboarding, and
  support tickets: attach the output and each value carries its source.
- **Doctor check "MCP orphan backstop"** (WARN-only, both twins): reports
  MCP servers mounted in a CLI's live config that have no source left in the
  manifest (leftovers from a retired server or a hand edit). Never removes
  anything: removal stays an explicit user action via the adopt/reset
  onboarding flow. First real catch on this machine: a leftover `node_repl`
  server in the Codex config.

### Changed

- **Configuration layer order is now documented and pinned**
  (`docs/sync-contract.md` -> "Configuration layer order" + regression
  tests): canonical manifest -> per-OS `windows:` override -> path
  placeholder expansion -> shim normalization -> runtime env expansion ->
  live additive preservation. Two behaviors are now explicit and verified:
  `env` is treated as one manifest-declared unit (a client-side env overlay
  is applied by the client at runtime, never merged into the generated
  config), and secret-named placeholders (`*TOKEN*`, `*KEY*`, ...) are never
  materialized even when the env var is set.
- **`seats.yaml.example` reads as a neutral product template.** The council
  seat-picking guidance no longer references the maintainer's own
  subscriptions; it now states the general criteria (prefer existing
  flat-subscription CLIs, cross-vendor review, zero-retention preference).

### Notes

- Playwright bump to 0.0.79 remains tracked as a dedicated task (see the
  maintainer's private plan note "nexgen-playwright-0.0.79-bump-plan"): the
  0.0.79 bundle refactored the shared-browser lifecycle and the
  CDP-disposal safety patch needs a re-engineered guard plus
  process-identity acceptance tests before it can land. Not urgent: the MCP
  protocol is unchanged, 0.0.78 works, and the doctor is green.

## [0.98.9] - 2026-08-16

### Changed

- **Firecrawl MCP bumped to `firecrawl-mcp@3.24.0`.** The reviewed upstream
  pin in `manifest.yaml` moves from 3.22.3: tool names are unchanged
  (`scrape`, `search`, `map`, `crawl`, `extract`), `firecrawl_developer_search`
  is new, and the `firecrawl_research_*` tools keep their names. `extract` is
  deprecated upstream (warnings only, still served) -- the engine's own
  docs/skills use `scrape`/`search`/`map`/`crawl`, so no public recipe
  changes. The pin is mirrored in `03-INFRA/firecrawl.md` and the manifest
  validation fixtures.

### Notes

- **Playwright stays at `@playwright/mcp@0.0.78`.** The 0.0.79 bump was
  attempted and rolled back: upstream refactored the shared-browser lifecycle
  (no more `backend.browserContext`), so the wrapper's CDP-disposal safety
  patch (the one that keeps an attached personal Chrome from being closed on
  client disconnect) fails its own unsafe-partial-patch guard. Bumping safely
  requires re-engineering that patch against the new bundle shape -- tracked
  as a dedicated task, not a pin edit.

## [0.98.8] - 2026-08-16

### Fixed

- **`agent-doctor.ps1` now anchors every content check to the resolved data
  root (`$VaultData`, the same `AGENT_VAULT_DATA` -> `KNOWLEDGE_VAULT_PATH`
  -> default chain the bash twin has used all along).** Five checks were left
  on `$Vault` during the split-topology fix of v0.98.3: the detail-notes
  budget, the load-on-demand pointer integrity, the vault-map backstop, the
  Mode gate, and the engine-pin read. On a split install (engine root and
  vault data in two different places) the Windows doctor scanned the wrong
  tree and reported false WARNs, ignored the declared Mode, and never saw
  the pin. The twin `.sh` already used `$VAULT_DATA` for all five.
- **`vault-groom.ps1` resolves Python like every other Windows twin instead
  of calling a bare `python`.** The audit gate (the step that can quarantine
  a bad clone and refuses a promotion) used a naked `python $AuditScript`,
  which fails with a cryptic error on a machine whose only `python` is the
  disabled Microsoft Store alias - after the user already confirmed with
  "yes". It now walks `py -3` -> `python3` -> `python` with a version probe
  and exits 2 with a clear message if none exists.
- **Four `.ps1` twins stop swallowing their real exit code under PowerShell
  7.3+.** `agent-sync.ps1`, `nexgen-update.ps1`, `council.ps1` and
  `agent-chrome.ps1` set `$ErrorActionPreference = "Stop"` without the
  `$PSNativeCommandUseErrorActionPreference = $false` guard the rest of the
  repo already uses (model: `vault-push.ps1`). Under pwsh 7.3+ a native
  command's non-zero exit became a terminating error, so `exit $LASTEXITCODE`
  was never reached and the wrapper always reported exit 1 with extra error
  text. No-op on Windows PowerShell 5.1.
- **Windows MCP configs route npm shims through `cmd.exe`.** `render.py`
  resolved `npx` to `npx.cmd` but left it as the emitted `command`; MCP
  clients that launch stdio via `CreateProcess` (native codex, antigravity)
  cannot run a `.cmd` shim (WinError 193), so the firecrawl, filesystem and
  memory servers silently failed to start in those CLIs. The generated
  config now uses `cmd.exe /d /s /c npx.cmd ...` for every resolved
  `.cmd`/`.bat`, the same contract `council.py`/`routing.py` already apply
  to their own subprocesses.
- **`render.py` honors `CODEX_HOME`.** Three codex paths were hardcoded to
  `HOME/.codex/config.toml` while `council.py` and `routing.py` already
  resolved `CODEX_HOME`; with the variable set, `--write` reported "WRITTEN"
  to a file Codex never mounts and the doctor's diff compared the wrong file.
  A shared `_codex_config_path()` now backs all three.
- **`agent-chrome.ps1` recognizes the profile owner case-insensitively.**
  The owner probe used an ordinal `Contains` on `--user-data-dir=$Profile`;
  Windows paths are case-insensitive, so a profile launched with a different
  case was not recognized and a second Chrome started instead of `--ensure`
  handing off.
- **`firecrawl-local.ps1` writes BOM-less UTF-8 and reports API errors
  cleanly.** `-Encoding utf8` under Windows PowerShell 5.1 writes a BOM that
  breaks downstream parsers on `-o` output; the write is now BOM-less
  UTF-8. API failures in `scrape`/`search` (401/500) no longer dump the raw
  PowerShell exception but a one-line message naming the endpoint and the
  two env vars to check.
- **Windows home-dir leak patterns cover every drive and both separators.**
  The anti-leak gate matched only paths under the C: drive Users directory; a
  path on another drive or with forward slashes passed clean. Patterns are now
  `[A-Z]:[\\/]Users[\\/]`,
  mirrored in the vault copy. (`leak_patterns.yaml` still lives in both
  places; rules must stay identical.)
- **`agent-doctor` makes the Antigravity probe model configurable.** The
  behavioral probe hardcoded "Gemini 3.5 Flash (Medium)" in both twins; if
  that model is renamed or absent the probe fails with a misleading FAIL
  instead of a skip. `NEXGEN_AGY_MODEL` overrides it in both `.ps1` and
  `.sh`.
- **`council.py`'s OpenCode cost probe goes through the Windows command
  wrapper.** It was the one subprocess in the project not wrapped by
  `_windows_command_argv`; on Windows with an npm `opencode` shim the probe
  failed silently to `{}` and the cost-based seat ordering was skipped
  without warning.
- **The two guardrail adapters spawn the guardrail body with
  `process.execPath` instead of a bare `node`.** With a portable Node
  install outside PATH, the hooks degraded to "ask" on every command; now
  they use the Node that is running the adapter itself.
- **`playwright-human-safe.mjs` retries its atomic rename on Windows
  locks.** Two MCP clients patching Playwright in parallel (or an IDE with
  the bundle open) raised `EPERM` on `fs.renameSync` and lost the server;
  the rename now retries with backoff for 3 s before giving up.
- **`vault-groom.ps1` switches the console code page to UTF-8, not just
  .NET's encoding.** On a legacy console (CP 850/437) the grooming tranche
  with accents/em dashes rendered as mojibake; `SetConsoleOutputCP(65001)`
  is now called best-effort (no-op under Windows Terminal).

### Changed

- `INIT.md` now states the OS command equivalents up front (both languages):
  every `python3 ...` in the guided flow is for Linux/macOS, Windows uses
  `python`/`py -3`, and `.ps1` twins replace `.sh` scripts. The
  `vault-map` skill doc carries the same note.

## [0.98.7] - 2026-08-16

### Fixed

- **The Windows doctor no longer FAILs healthy hosts for three
  PowerShell-5.1-only bugs.** (1) Inline `python -c` code used double
  quotes, which PS 5.1 strips when passing arguments to a native exe:
  a valid `opencode.json` was reported as invalid JSON/JSONC. The code
  now uses single quotes. (2) The vault-library URL derivation piped the
  native call into `Select-Object -First 1`, which closes the pipe after
  the first line and kills the child before it reports its exit code
  (`$LASTEXITCODE = -1`): the endpoint was flagged "cannot be derived"
  even when real. Output is now captured in full first, then trimmed,
  and the same capture-first shape applies to the vault-map backstop and
  the out-of-manifest skills count. (3) `$HomeDir` now honors an
  explicit `$env:USERPROFILE` before falling back to the OS profile,
  matching the bash twin's `$HOME` semantics: a redirected home (test
  sandbox, session profile) is inspected instead of the real user's
  `~/.config`, `~/.claude`, `~/.codex` and `~/.gemini`.

### Changed

- **"Claude is not authenticated" is now a WARN, not a FAIL**, in both
  twins, matching the existing OpenCode/Codex/Antigravity treatment of
  absent CLIs: a logged-out Claude is a host-local state, and it was the
  one FAIL a machine could not clear without an interactive login.
  `agent-sync --require-ready` and `nexgen-update` compare FAIL counts,
  which only go down.

## [0.98.6] - 2026-08-16

### Fixed

- **The failed Logon scheduled task is no longer retried every cycle.**
  The Logon trigger of the sync task fails with `ERROR_ACCESS_DENIED`
  on some Windows builds (quoted `/TR` handling in `schtasks.exe`), and
  the guard retried that `/Create` every 30 minutes forever, keeping the
  Defender `Trojan:Win32/Commando.A!ml` signal alive even after the
  idempotence fix for the main task. The failure is now remembered per
  wrapper-hash: first run attempts once, later runs with an unchanged
  wrapper skip the `/Create` entirely (the Startup-folder VBS copy still
  covers logon), a changed wrapper retries.

## [0.98.5] - 2026-08-16

### Fixed

- **The Windows scheduled-task guard no longer re-creates an unchanged
  task every cycle.** `agent_sync.py` re-ran `schtasks.exe /Create /F` for
  the "KnowledgeVault Agent Sync" tasks on every guard run (every 30
  minutes), rewriting identical definitions. Windows Defender's behavioural
  engine flags that exact persistence pattern (recurring schtasks + hidden
  VBS wrapper + PowerShell `-ExecutionPolicy Bypass`) as
  `Trojan:Win32/Commando.A!ml` -- a false positive on the engine, but a
  legitimate-looking signal: a routine that rewrites its own scheduled
  task on every cycle is how real trojans persist. The task is now queried
  with `schtasks /Query /XML` (locale-independent) and rewritten only when
  the wrapper path differs or the task is absent; a content-only wrapper
  update needs no rewrite because the scheduler executes the file at the
  recorded path. Regression test proves an unchanged task triggers zero
  `/Create` runs.

## [0.98.4] - 2026-08-15

### Changed

- **No commercial license is offered anymore.** `COMMERCIAL.md` no longer
  describes how to request a paid commercial license: the project is
  noncommercial only, and the noncommercial condition carries into
  derivatives (already true under PolyForm Noncommercial 1.0.0). README,
  INIT, docs/team and docs/org-deployment now say "not licensed" instead of
  "ask for a license". An eventual company-specific product would be
  written from scratch, outside this repository.

### Fixed

- **Every `.ps1` is ASCII-pure (or UTF-8-with-BOM), so Windows PowerShell
  5.1 can never misparse one again.** `agent-doctor.ps1` (and four other
  `.ps1` twins) were UTF-8 without BOM, and PS 5.1 decodes a BOM-less
  `.ps1` as ANSI/cp1252: the bytes of an em-dash (E2 80 94) spell a smart
  closing quote that the parser treats as a string delimiter, making the
  whole file fail to parse with "Token '}' unexpected". `nexgen-update`
  then died in its pre-upgrade doctor step on every Windows machine from
  v0.98.0 to v0.98.3, and the Linux pwsh-7 lint job could never see it. A
  cross-platform byte check (`test_every_repo_ps1_is_ascii_pure_or_utf8_bom`)
  now locks the invariant in on every runner.

## [0.98.3] - 2026-08-15

### Fixed

- **`agent-sync` no longer destroys a hand-written file at a link target
  (POSIX).** `make_link()` backed up a real file before replacing it with a
  symlink on Windows only; on POSIX the file was unlinked with no backup, so
  a hand-edited `~/.codex/AGENTS.md` or a manually configured
  `mcp_config.json` was silently lost. The backup now runs on every
  platform, is deduplicated (an identical `.bak` is not re-created), is
  bounded (the three newest backups are kept), includes the PID in the name
  so concurrent sync runs cannot collide, and a failed backup now reports
  itself through a callback instead of looking like "nothing to do".
- **The Council no longer hangs forever when a seat closes its output and
  lingers.** `run_seat`'s `proc.wait()` after stdout EOF had no timeout, so
  a seat that flushed its answer and stalled in teardown blocked the round
  indefinitely. The wait is now bounded by the seat's remaining budget
  (with a 10s grace), the process is killed on expiry, and a complete
  verdict that already arrived is still returned instead of being thrown
  away. A truncated or missing output file degrades to a clean
  `partial_timeout` diagnostic, never an unclassified crash.
- **`agent-now` on Windows reports NTP state without parsing localized
  text.** `w32tm` labels and values are translated ("Sorgente:",
  "Orologio CMOS locale", ...), so the old `Source:` regex left the report
  on "unknown" (or worse, a false "yes" on non-English Windows). The check
  now reads the W32Time registry `Type` (untranslated enum) and the
  Time-Service event log (numeric event IDs): last known state decides
  between synchronized / desynchronized, transient "cannot reach" events
  are ignored, and the service's start type (not its transient Running
  state) drives `ntpEnabled`.
- **`agent-open-folder` on Windows opens folders with spaces and drive
  roots.** PS 5.1 does not quote `-ArgumentList` items, so explorer.exe
  split paths containing spaces, and hand-rolled quoting broke on trailing
  backslashes. The folder is now opened via `Invoke-Item -LiteralPath`
  (ShellExecute, no command-line parsing at all).
- **`vault-groom` resolves the vault target the same way on both
  platforms, and refuses to groom a missing tree.** The PowerShell twin
  ignored `AGENT_VAULT_DATA`/`KNOWLEDGE_VAULT_PATH` and could groom the
  wrong (or nonexistent) tree on split-topology installs; the bash twin
  ignored `KNOWLEDGE_VAULT_PATH`. Both now use the same resolution chain
  as `agent_sync.py`, print which variable won and where it points, warn
  loudly on conflicting variables, and exit 3 before any
  rename/move/delete when the resolved target does not exist. Path
  validation is wrapped so an illegal path value produces the friendly
  message, not a raw .NET traceback.
- **The OpenCode guardrail plugin registers with a valid file URI on
  Windows.** The old `file://` + backslash spelling (`file://C:\...`)
  parsed with host `C:` and backslash path characters, so the plugin
  could not load while the permissive posture was still applied. The
  entry is now `Path.as_uri()` (three slashes, forward slashes), existing
  legacy spellings are migrated in place, repeated entries are
  deduplicated, and non-path entries are never resolved against the
  process working directory.

## [0.98.2] - 2026-08-15

### Fixed

- **`firecrawl-local status` no longer prints the API key.** When
  `FIRECRAWL_API_KEY` was set, the shell expansion `${VAR:-default}` echoed
  the actual key value after the `env FIRECRAWL_API_KEY` label, landing the
  real key in stdout and any log that captured it. The auth line now prints
  only the label; the twin `firecrawl-local.ps1` already behaved correctly.
- **MCP wrapper processes no longer linger after their child dies by
  signal.** `mcp-http-bridge.mjs` and `playwright-human-safe.mjs` re-sent the
  child's termination signal to themselves from the `exit` handler, but the
  forwarding handler registered for the same signal consumed it and returned,
  leaving the wrapper alive forever. The listener is now detached before the
  re-signal, and a truthful `128+signal` exit code is set first so a signal
  with an "ignore" default disposition (SIGPIPE) still reports the child's
  fate instead of a fake 0.
- **`agent-chrome --ensure` with no further arguments no longer opens a
  phantom tab.** The PowerShell argument slice `[1..(Count-1)]` on a single
  argument produced the descending range `1..0`, keeping `--ensure` in the
  list, which was then passed to Chrome as a stray positional. Replaced with
  `Select-Object -Skip 1`.
- **`agent-sync` no longer mistakes Windows junctions for unlinkable
  state.** `vault_skills()` used `Path.is_symlink()`, which is `False` for
  directory junctions, so a broken whole-root junction made the apply crash
  instead of being repaired. The new broken-junction-only predicate repairs
  exactly the recoverable state and leaves working junctions and cloud
  placeholder folders untouched.
- **`agent-doctor` no longer reports a permanent FAIL for Codex and
  Antigravity on a machine where they were never installed.** Both twins
  now emit a WARN for a genuinely missing instructions file on an absent
  CLI, matching the existing OpenCode treatment. A present-but-divergent
  file still fails hard regardless of PATH.
- **`agent-sync guard` skips are now visible in the journal.** When the
  host-wide lock was held past the wait window, guard exited 0 having done
  nothing and looked identical to a successful run in `journalctl`. The skip
  now prints to stderr.
- **`_process_running()` detects npm-installed CLIs on Linux.** Claude Code
  installed via npm runs as `node`, so `pgrep -x claude` missed it and
  `mcp-gen` rewrote `.claude.json` while Claude was live. The check now falls
  back to matching `node` processes by name, mirroring the Windows branch.

## [0.98.1] - 2026-08-14

### Fixed

- **A web app started from the dock no longer kills the shared browser's CDP
  port.** The first Chrome process to open the shared profile decides whether
  `:9222` exists for the rest of the session, and Chrome's generated
  `chrome-<app-id>-<profile>.desktop` launchers called the Chrome binary
  directly, with no debugging port. Starting WhatsApp or n8n before the browser
  therefore won that race, and every later launch — `agent-chrome` included —
  degraded to a single-instance IPC handoff, which cannot add a port to a
  running browser. The whole shared-browser lane then stayed dead until Chrome
  was restarted, silently: the web app opened perfectly, only the agents were
  locked out. `agent-sync` now rewrites those `Exec=` lines to the launcher,
  and re-applies the repair on every `guard` run because Chrome regenerates the
  files on each web-app install or update.
- **Clicking the Chrome icon opens a window again.** A bare `agent-chrome` meant
  "make sure the browser exists" and exited 0 when CDP answered, so with a
  windowless Chrome holding the port the click did nothing at all and the
  desktop recorded a successful launch.
- **An installed web app is no longer driven as a general browser.** A shared
  Chrome exposes web apps over CDP as ordinary page targets, so Playwright MCP
  adopted whichever it enumerated first as the current tab and would navigate
  the user's application window away from what they had open.
- **A web-app launcher path containing a space produced an unparseable `Exec`
  line.** Quoting used POSIX single quotes; the Desktop Entry Specification
  requires double quotes with `"`, backtick, `$` and `\` escaped.

### Added

- **`agent-chrome --ensure` and `--heal`.** `--ensure` is the idempotent "make
  sure the shared browser is up" call that opens no window, which is what a
  bare call used to do. `--heal` restarts a Chrome that already lost the CDP
  race, the one state a handoff cannot repair. Both dialects.
- **`agent-doctor` reports the shared-browser race.** It flags both the live
  state — a Chrome holding the profile without CDP — and its structural cause,
  a web-app launcher that calls the browser binary directly.

### Changed

- **A bare `agent-chrome` now always hands off to Chrome**, so a desktop
  activation always produces a window. Callers that wanted the old no-window
  behaviour should use `--ensure`.
- **`agent-sync` rewrites the `Exec=` lines of Chrome's generated web-app
  launchers** on Linux, and `--class=Google-chrome` is no longer forced on an
  `--app-id` launch so each web app keeps its own window class and dock icon.
  Declared in `docs/what-gets-written.md` and reverted by `docs/uninstall.md`.
- **The Playwright wrapper separates using a web-app window from adopting it.**
  Pages are classified by `display-mode`, so no per-application URL list is
  maintained. An app window stays listed and can be selected deliberately — an
  open WhatsApp window is the right place to send a WhatsApp message — but it is
  never adopted implicitly as the current tab and cannot be navigated off its
  own origin.

## [0.98.0] - 2026-08-06

### Fixed

- **`render.py --revert` can restore an OpenCode config again.** The backup was
  validated as strict JSON or TOML with no `.jsonc` branch, so the commented
  backup `render.py` had just written was rejected as corrupt by the command
  whose job is to restore it. Because that check runs before the "file is
  absent" branch, `--reset opencode` followed by `--revert opencode`, the
  documented undo pair, left the config unreachable rather than un-reverted.
- **leak-scan now catches PKCS#8 private keys.** The PEM pattern listed only
  the `RSA`, `OPENSSH`, and `EC` headers, so a key with no algorithm word,
  which is what `openssl genpkey` emits by default and what a GCP
  service-account JSON carries, passed the gate.
- **leak-scan now reads files Git classifies as binary.** Both automatic lanes,
  the pre-commit hook and the CI `--commit-range` gate, omitted `--text`, so
  Git printed "Binary files differ" with no added lines and the scan exited
  clean on a blob that carried a secret. The manual `--tree` mode was
  unaffected.
- **`vault-push` commits only the files it was given.** `git add` was scoped to
  the named paths but the emptiness probe and the commit were not, so anything
  already staged by the caller was swept into a commit whose message named a
  single file. The engine-pin write goes through this path and is documented as
  carrying only that file. Other staged entries now stay staged.
- **A failed `nexgen-update` no longer leaves a half-merged tree.** A conflicted
  merge raised past every cleanup path, parking conflict markers inside
  `AGENTS.md` and the generated CLI configs, which are the files the CLIs read
  as instructions. The merge is now rolled back before the failure is reported.

### Changed

- **Release tags are cut by the maintainer, and CI refuses unsigned ones.**
  `release.yml` no longer creates the tag on a runner. It triggers on a pushed
  tag and rejects anything that is not an annotated object carrying a signature
  block, so an unsigned release cannot reach the Releases page. Cutting a
  release is now `git tag -s vX.Y.Z -m "Release vX.Y.Z" && git push origin
  vX.Y.Z`. A warn-only `release-pending` job on `main` reports when `VERSION`
  runs ahead of the newest tag, which is the risk the previous auto-tagging
  covered.
- **`SECURITY.md` now describes the repository as it is.** It claimed every tag
  from `v0.3.1` onward was signed; fourteen were not, including the twelve
  consecutive releases from `v0.93.0` to `v0.97.6` that a CI runner tagged
  without a key. It also presented branch protection on `main` as active when
  the branch was unprotected. Protection is now configured, and the signing
  section lists the real gaps instead of asserting a clean history.

## [0.97.6] - 2026-08-01

### Fixed

- **Claude seats now enter Governor proposals when the CLI can select their
  declared model.** Council checks the installed Claude CLI for explicit
  `--model` support, plus `--effort` when a seat requests it. The actual
  invocation uses JSON output and verifies `modelUsage`, so a missing model
  identity or a silent provider fallback stops the run.

## [0.97.5] - 2026-08-01

### Fixed

- **The bare updater now discovers the normal Vault data root.** Without
  exported variables, `nexgen-update` now follows the same path priority as
  the rest of the engine: `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`, then
  `~/KnowledgeVault` when it is a Git checkout. A split installation can no
  longer be mistaken for a single clone merely because the command was opened
  from a fresh terminal.

## [0.97.4] - 2026-08-01

### Fixed

- **The updater now respects both supported Git topologies.** A split
  consumer engine remains fast-forward-only, while the default single clone
  may create a normal merge commit to preserve legitimate private data commits
  that do not overlap the released engine files.
- **A split consumer's private engine pin advances with the release.** When
  `99-INDEX/ENGINE-PIN.txt` already exists, `nexgen-update` requires
  `vault-push`, shows the pin update in its confirmation plan, and commits only
  that mechanical file before provisioning. A missing publisher or failed pin
  publication stops visibly instead of leaving doctor with a permanent version
  mismatch.
- **Single-clone merges validate Git identity before confirmation.** An install
  that needs a merge commit now reports the missing `user.name` or `user.email`
  before moving anything.

## [0.97.3] - 2026-08-01

### Added

- **`nexgen-update` is now a real cross-platform terminal command.** The
  provisioner installs a POSIX launcher plus native PowerShell and CMD
  launchers, all backed by one Python updater. `--check` does not move the
  branch or installed files, while a real update shows the exact tag and
  changelog, requires confirmation, merges only as a fast-forward without
  detaching `HEAD`, provisions the generated layer and compares the doctor
  before and after.

### Fixed

- **A dirty split data clone now blocks before the engine ref moves.** The
  updater checks both the consumer engine clone and `AGENT_VAULT_DATA`, so a
  personal note or untracked file cannot leave the machine half-upgraded.
- **Doctor's `run: nexgen-update` advice now names an executable that exists.**
  The chat skill remains available as `/nexgen-update`, but the bare command is
  no longer an undocumented manual procedure disguised as a launcher.

## [0.97.2] - 2026-08-01

### Fixed

- **Council now reads the current Governor output.** The public Governor emits
  one `Slot | Modello | CLI | $ | Motivo` table per role, while Council still
  expected the retired flat table and stopped at `routing-status`. The resolver
  now parses the per-role format, preserves its fallback order, and represents
  an explicitly unassigned role without borrowing candidates from another role.
- **The same model on two CLIs no longer collapses into one route.** Governor
  candidates carry both model identity and CLI into the local resolver. A legacy
  document without CLI metadata still works when the mapping is unique, but
  fails closed with a diagnostic when several CLIs match.
- **Malformed routing documents fail closed more consistently.** Exact Governor
  headings are required, duplicate roles and candidates are rejected, and only
  a date-shaped build suffix can map to a stable routing ID. Without a Governor,
  declared `agy` seats are shown as disabled instead of being presented as an
  invocable choice.

### Changed

- **Missing zero-retention is warning-only.** Seats with
  `zero_retention: false` remain visible and runnable. Council marks them in
  proposals and status output, then warns on stderr immediately before the
  provider call. The old flag and preference command remain accepted as no-op
  compatibility shims.
- **Optional reference projects are linked from the main documentation.**
  Council still works from `seats.yaml` alone. Users who want dynamic routing can
  add the public LLM Model Routing Governor. The semantic-search section now
  links the public Hybrid RAG implementation as well as the in-repo recipe.

## [0.97.1] - 2026-07-31

### Fixed

- **The shipped grooming playbook created four broken wikilinks in every
  install.** It illustrated the syntax in prose — a bare double-bracket
  example inside backticks, four times, in sentences about repairing links —
  and `vault-map.py` deliberately does not strip inline code spans, because
  backtick-wrapped wikilinks are a legitimate way to link in a real vault. So
  an *example* was indistinguishable from a live edge: four phantom broken
  links that no note could ever satisfy, reported by `agent-doctor` on every
  run since the check shipped. A WARN nobody can clear is how people learn to
  ignore WARNs — the same lesson as 0.95.2, one layer up. The prose now says
  "wikilink" as a word, and a new test scans this repo's own shipped docs with
  the very tool it ships and fails the build on the next one.

## [0.97.0] - 2026-07-31

Three reports from people running the engine rather than building it.

### Fixed

- **The seven starter commands never existed on any fresh install.** `README.md`
  says they ship with the engine, each one is vendored and tested — and nothing
  ever created the `skills.manifest.yaml` that turns them into runtime views.
  `skills-sync.py` printed "manifest not found ... skipping" to stderr and
  exited 0, `INIT.md` told the installing agent there were no base skills and to
  skip the step outright, and the doctor's only signal was "no managed skill
  (fresh install)" — worded as normal. Five cold installs shipped without
  `/vault-doctor` or `/nexgen-update`, the second being the only upgrade path a
  non-technical user has.
  `agent-sync` now seeds the manifest from the shipped
  `skills.manifest.yaml.example`, and only ever when the file is absent
  entirely: an existing manifest is never rewritten, so emptying it
  (`skills: {}`) is a permanent opt-out, and a split engine/data topology whose
  skill bodies live in the other clone is left alone rather than handed seven
  entries that resolve to nothing. `INIT.md` no longer contradicts the README.
- **`agent-chrome` on Windows never reused the shared browser.** The POSIX
  launcher probes `127.0.0.1:9222` and exits cleanly when the shared Chrome is
  already up — that is the whole reuse rule in `03-INFRA/agent-browser-cdp.md`.
  The PowerShell twin, added in #46, never had the probe: a bare `agent-chrome`
  started a second Chrome process every time and blocked its caller until the
  user closed the window. Both launchers also pass `--no-first-run` now, so
  Chrome's first-run flow on the freshly created CDP profile cannot steal focus
  from an agent that was told to start the browser without asking.
  (Reported against 0.96.0 as a regression; it is not one — the file never had
  the branch. `--profile-directory=Default` was in the same report and is
  deliberately not added: it names the directory Chrome already uses.)

### Added

- **The doctor says when the VPS is behind.** A Cloud-Server install is two
  installs: upgrading a workstation clone also moves `03-INFRA/deploy/`, but the
  server keeps running the containers it was last deployed with, and nothing
  said so — the maintainer redeployed by hand every release and an end user had
  no way to know it was a step at all. `agent-doctor` (both twins) now reads the
  version `vault-mcp` reports on its own unauthenticated root route and compares
  it with the source in this clone: `vault-mcp on the server is X but this
  engine ships Y`. A WARN, never a FAIL — when to restart services that live
  agents are writing through is the owner's call. `docs/upgrade.md` gained the
  Cloud-Server half it never had, `03-INFRA/deploy/README.md` gained the
  redeploy runbook it points at, and `/nexgen-update` now names the server half
  without offering to do it.
- **The doctor says when the starter commands are missing**, by name and with
  the command that installs them, instead of leaving it to a WARN about managed
  skills that reads as a normal fresh install.

## [0.96.0] - 2026-07-31

### Fixed

- **`agent-sync apply` failed for as long as Claude Code stayed open.** The
  provisioner deliberately does not rewrite `.claude.json` under a running
  Claude, but the drift that leaves behind was still counted against the run
  that chose to leave it: one global counter, read off the renderer's
  human-readable summary line, could not express "this CLI was skipped on
  purpose". With Claude open and any pre-existing drift in its config, every
  apply reported failure and re-running never helped.
  `render.py` gained `--json`: the same scan, the same exit codes, reported per
  CLI instead of as one total. The human report is produced by the same
  function and is unchanged. `agent-sync` now excludes exactly what it skipped
  and nothing else — drift on any other CLI still fails, and so does Claude's
  own drift once Claude is closed — and says out loud what it deferred.

### Added

- `render.py --json`, with the default diff: a machine-readable per-CLI drift
  report. Written because the alternative was parsing a report meant for
  people on the code path that decides whether a sync passed.

## [0.95.2] - 2026-07-31

### Changed

- **A bypass posture without a guardrail is a WARN, not a FAIL.** Nobody reaches
  that state by accident: it exists only because the instance manifest
  deliberately declares the posture for a CLI and no guardrail hook targeting
  it. That is a standing choice, not a fault. As a FAIL it could never be
  cleared, so the doctor stayed permanently red, `agent-sync apply` could never
  report `READY`, and the alert channel fired on every run — which is precisely
  how a real FAIL stops being read. It is still reported on every run, by name,
  per CLI.

## [0.95.1] - 2026-07-31

### Fixed

- **The guardrail adapters made every ordinary command ask for confirmation.**
  A Claude PreToolUse hook permits a tool by exiting 0 and writing nothing; it
  only speaks up to deny. Both new adapters read that silence as unparseable
  output and fell back to asking, so on OpenCode and Antigravity a posture
  that removes confirmation prompts quietly reinstated them for everything
  except the commands the guardrail actually blocks. Found by running the
  installed adapter against a real guardrail body rather than a test double,
  which is now what the suite does for the allow path too.

## [0.95.0] - 2026-07-31

A pass over what a stranger meets in the first five minutes, plus the
provisioning failure that let a whole CLI stay unconfigured forever.

### Added

- **The guardrail reaches OpenCode and Antigravity.** Until now a posture that
  removed every confirmation prompt was only ever backed by a brake on Claude,
  because Claude was the only CLI whose pre-execution hook this engine knew how
  to speak. A manifest guardrail hook can now target `opencode` and
  `antigravity` too: a thin engine-owned adapter per CLI translates their
  native hook contract to and from the same stdin/stdout shape a Claude
  guardrail body already speaks, so one guardrail body serves every CLI and
  the dangerous-command logic is never duplicated. The ordering that matters
  is unchanged and now applies to all three: if a declared guardrail cannot be
  installed, the no-prompt posture for that CLI never reaches disk.
  Codex is deliberately excluded. Its hook mechanism gates every hook behind a
  per-hash trust prompt that a provisioner cannot satisfy on its own, and the
  alternative — passing its bypass-trust flag — would install a brake that only
  looks like one. Declaring a Codex guardrail is a hard manifest error rather
  than a half-working install.
  The adapters fail closed: a sidecar that exists but cannot be parsed falls
  back to asking for confirmation, never to allowing, and a guardrail body that
  crashes or answers nothing usable does the same. The OpenCode plugin
  registers its handler whether or not any guardrail is configured yet, so a
  session already running when the guardrail is installed is covered from its
  next command rather than never.
- Permission posture renderers for Codex, OpenCode and Antigravity. The phase
  used to translate a posture for Claude only, so a manifest that named
  another CLI was policy the engine could read and never apply. Each dialect
  was verified against the installed binary rather than inferred; a CLI whose
  dialect is not verified is skipped with a warning, never guessed at.
- `agent-doctor` now lists which CLIs run under a `bypass` posture and which of
  those have no guardrail hook behind them. A posture that removes every
  confirmation prompt should be visible on every health check, not something
  you have to remember you once chose.
- `vault-save` and `vault-close` work without a server. Both required the
  `vault-library` MCP with no alternative, and `vault-close` told the agent to
  stop outright if the vault was unreachable — on a server-less install the
  vault is an ordinary writable folder, so two of the seven advertised
  commands were dead for no reason.
- The `vault-mcp` package declares its license and the image carries `LICENSE`,
  as the license itself requires of every distributed copy.
- `council/seats.yaml.example` is validated against the runtime schema by a
  test, so the shipped example cannot drift into something that fails to load.

### Fixed

- **A CLI that was installed but never launched stayed unconfigured forever.**
  `render.py` skipped provisioning when the config file did not exist, and
  nothing else ever created it, so on a fresh machine Antigravity received no
  MCP server at all — indefinitely, with the inventory reporting "not
  configured" as if that were a steady state. Antigravity and OpenCode now
  bootstrap the directory and a minimal valid config, then provision normally.
  A CLI that is genuinely absent is still skipped, and an empty or truncated
  config file is initialized instead of aborting the run.
- **A missing environment variable removed MCP servers silently.** A server
  gated by `require_env` was dropped from every CLI with a message that never
  named the variable. `render.py` now names it, and `agent-doctor` raises an
  explicit warning saying which variable is missing and which server is
  therefore mounted nowhere — while staying quiet on a Local-Only install,
  where those servers are absent by choice.
- **`agent-sync apply` was not idempotent.** The `instructions` phase ran
  before `mcp_render`, so a config file bootstrapped during a run received its
  `AGENTS.md` pointer only on the *next* run — self-healing where a timer
  exists, permanent on MINIMAL, which has none. Compounding it, Antigravity's
  "is it installed" probe keyed on `~/.gemini`, a directory `agent-sync`
  creates itself, so the provisioner was reacting to its own footprint. The
  probe now looks for the product's own files, and the phases are ordered so
  one apply converges.
- **One unsupported entry could fail the entire permissions phase.** A posture
  naming a CLI the engine had no renderer for was refused wholesale, taking
  down everything the engine *could* apply and failing every scheduled run
  with it. Unknown CLIs are now warned about and skipped. The hard refusals
  that matter are untouched: a malformed manifest, an unknown posture value,
  and a declared guardrail hook whose body is missing or resolves outside the
  permissions directory still stop the phase before anything reaches disk.
- Two false FAILs on a server-less install. `agent-doctor` reported commits
  behind "the cloud" and told the user to publish private notes, because their
  `origin` is this public repo. `INIT.md` now sets `authoritative_remote:
  local` for that install shape, and the two checks respect it.
- `agent-doctor` contradicting itself within one run, warning about an absent
  `VAULT_LIBRARY_URL` and then reporting twenty lines later that it is not
  expected in this mode.
- `--reset` deleted a CLI's config with nothing able to recreate it, which for
  Claude meant being logged out; it now refuses for those CLIs. `--revert`
  after a reset also failed to find its own backup for OpenCode, whose config
  path is resolved from whichever filename exists — once the file was gone,
  the resolution no longer matched the backup's name.
- `~/CLAUDE.md` was overwritten without a backup on every platform except
  Windows, contradicting the documented promise.
- The recurring timer was armed even when the command it must run was missing,
  leaving a task firing every thirty minutes at nothing.
- The installer's preflight accepted any Python 3.x while the engine requires
  3.11+, so 3.9 and 3.10 passed the check and failed mid-install. It also
  accepted unknown arguments silently: `-check`, with one dash, ran the
  writing path.
- `leak_scan.py` scanned virtualenvs, so the documented maintainer command
  returned over a thousand results and buried the handful that were real.
- `.gitignore` did not cover `.venv`, `venv`, `node_modules`, `dist`, `build`
  or the tool caches. A virtualenv created inside the checkout escaped only
  through the ignore file the venv writes for itself.

### Changed

- Documentation now matches the code where it had drifted: how skills reach
  Codex, the Antigravity skills path, the attribution of `--revert`/`--adopt`,
  the `council allow-training` subcommand that existed but was documented
  nowhere, and the backup files that are kept rather than rotated. `README`
  states that `profile:` is an installer convention no code reads, instead of
  describing behavior that does not exist.
- `CREDITS.md` credits the skills the repo actually ships and no longer credits
  three it does not.
- Prose written for the maintainer is written for the reader instead: a
  specific machine fleet, personal subscriptions presented as the product's
  cost model, a personal TODO list inside an architecture document, an
  unreachable reference to a past incident, and a generic user who was
  systematically male.
- `README` points to its own Italian section from the top instead of leaving
  it discoverable only by scrolling past the English half, the way `INIT.md`
  already does. The Italian "Percorso demo" now carries the same explicit
  `git clone` command and target directory as the English "Demo path" it
  mirrors, and both language pairs of "Demo path"/"Installation" now
  cross-reference each other's complementary step (the clone command, the
  `agent-doctor` drift check). "Platform status"'s Windows and AI Council
  paragraphs are trimmed from an engineering log to the practical limitation
  and current workaround, in both languages; the forensic detail they used to
  carry stays in this changelog instead.

## [0.94.0] - 2026-07-30

### Added

- Codex now receives a native per-skill view under `$CODEX_HOME/skills`. It was
  previously assumed to discover skills through the shared `~/.agents/skills`
  root, which it has never read: verified against the shipped binary, its only
  skill root is its own. A declared `targets: [codex]` therefore produced zero
  discoverable skills. Bodies stay lazy — Codex loads name and description and
  opens `SKILL.md` only when the skill is used. The shipped example manifest
  now declares the target on its starter entries, so a fresh install gets the
  commands it advertises.
- New `claude_permissions` provisioning phase: it carries a permission posture
  and its guardrail hooks from your private vault into Claude's settings, on
  every machine. The engine ships only the mechanism. Without a
  `permissions/manifest.yaml` in your own vault the phase writes nothing at
  all, so no installation inherits anyone else's posture. A `PreToolUse` hook
  rather than a deny list, because under `bypassPermissions` the permission
  engine is skipped entirely and deny rules may never be consulted, while the
  hook still runs. Any anomaly refuses the whole phase: a machine is never left
  with prompts disabled and its guardrail unregistered.

### Fixed

- `agent-sync` no longer hides that it shadows your distribution's Chrome
  launcher. The hidden `google-chrome.desktop` it writes is load-bearing — a
  plain Chrome started from the dock wins the first-process race with no CDP
  port and the shared-browser lane stops working — but it was undeclared, and
  the function's own docstring described the opposite. It is now documented in
  `docs/what-gets-written.md` and removed by `docs/uninstall.md`, which also
  documents the two distinct backup naming conventions in use.
- `test_apply_is_idempotent` no longer fails on wall-clock timing alone. The
  Firecrawl search-health cache carries a TTL, so a run after it expires wrote
  the file and sometimes created its parents only on the second pass.

- MULTI installs now report three explicit states. `BASE` means the available
  components were installed, `PARTIAL` means strict checks still fail, and
  `READY` requires `agent-doctor --strict` with `FAIL=0`. Plain apply keeps a
  progressive `PARTIAL` install usable; `agent-sync apply --require-ready`
  makes that state fail in automation.
- The MULTI provisioner now installs the cross-platform `agent-chrome`
  launcher that its browser policy already required. It starts one visible
  Chrome profile with CDP bound to localhost and refuses to create a second
  daily profile when the standard profile has not been migrated.

### Fixed

- OpenCode 1.18 creates `opencode.jsonc`, but sync, renderer, and doctor looked
  only for `opencode.json`. Existing OpenCode authentication survived while
  its instructions and MCP servers were silently skipped. All three consumers
  now follow OpenCode's config precedence, preserve JSONC comments and
  host-local choices, and find the standard per-user OpenCode binary from the
  systemd timer.

## [0.93.0] - 2026-07-30

### Added

- Releases now publish themselves. A workflow tags and creates the GitHub
  release whenever `VERSION` changes on `main`, so bumping `VERSION` is the
  whole act of releasing. It is not a gate: it never fails a pull request, a
  `VERSION` touch that introduces no new version exits quietly, and a missing
  changelog section warns and still publishes. This release is the first one it
  publishes. Before it, work merged to `main` and stopped there — the update
  path compares the newest release tag with the installed `VERSION`, so
  unreleased commits reached no machine at all and the doctor still reported
  every host "at the latest released version".

### Changed

- Both bundled MCP servers now implement the `2026-07-28` protocol revision
  while continuing to serve every earlier one, so clients that still open with
  the retired `initialize` handshake keep working unchanged.
  - `vault-library` moved to MCP Python SDK 2.x (`MCPServer`, transport
    configuration passed where the ASGI app is built) and its CORS allowlist
    now accepts the `Mcp-Method`, `Mcp-Name`, and `MCP-Protocol-Version`
    headers the revision requires.
  - `vault-ocr`, which speaks JSON-RPC directly, gained `server/discover`, the
    required `resultType` on every result, `ttlMs`/`cacheScope` on cacheable
    results, `serverInfo` in each result's `_meta`, and refuses an unsupported
    protocol version with `-32022` instead of serving it. No method is gated on
    a prior handshake.

### Changed

- `agent-doctor`'s update notice leads with what it is and carries the command
  to run: "NeXgen Engine update available: v0.93.0 (this machine runs v0.92.1)
  -- run: nexgen-update". It used to end with "update is always deliberate",
  which reads as "no rush" and let a host drift several releases behind
  before anyone acted on the daily notice. Still a warn, never a failure:
  being one release behind is not a broken machine. Both the bash and
  PowerShell twins changed.
- The engine-update command is now `nexgen-update`. It upgrades the **engine**,
  and the old `vault-update` name said the opposite clearly enough that a user
  looked for it, did not find it, and concluded the command did not exist. The
  previous name stays registered as a stub that defers to the new one, so an
  existing `skills.manifest.yaml` keeps resolving; the upgrade runbook lives in
  exactly one of the two, so they cannot drift apart. Starter skills now only
  have to be namespaced rather than specifically `vault-`-prefixed — the point
  of the prefix was never to claim the Vault, it was to avoid shadowing a CLI
  built-in.

### Fixed

- The `vault-mcp` compose file's default image tag now tracks the package
  version again. It had drifted to `0.1.0` while the package moved on, which
  silently voided the rollback recipe documented in the same file: every
  rebuild overwrote the one tag, so no previous image was ever left to return
  to. A test pins the tag to `__version__`, and the sibling OCR stack's tag to
  its API version, since nothing breaks visibly when they part company.
- `.gitignore` now covers a bare `.bak` suffix, not only `.bak-<timestamp>`. A
  browser-profile backup left in `03-INFRA/` — session cookies, the saved
  password database, its encryption keys — was not ignored, one `git add -A`
  from a permanent history. The known-sensitive profile filenames are covered
  explicitly too, since the layout invites infrastructure runbooks there.
- `agent-sync inventory` reports whether each CLI's bootstrap is actually
  aligned with the canonical one, instead of printing `present` for any file
  that exists. On a host without symlink privilege the per-CLI bootstrap is a
  real copy, and a stale copy was indistinguishable from a fresh one — the CLI
  reading it looks confused rather than out of date. A diverged copy now says
  so and carries the recovery command; an identical copy is reported as a copy
  that re-aligns only when agent-sync runs. Claude's pointer file, which is
  meant to differ from the canonical bootstrap, is judged by reference rather
  than by content. The copy fallback also stops being silent: the sync log now
  states that the file is a copy and that a canonical edit stays invisible to
  that CLI until the next run.
- `INIT.md` no longer lists `agent-healthcheck` among the commands not
  installed in MINIMAL, which implied it exists in FULL. There is no such
  command — the healthcheck is a step inside `agent-sync`.

## [0.92.3] - 2026-07-23

### Fixed

- The optional Brave-backed SearXNG engine now stops at its first 20-result
  page. Firecrawl deliberately requests a larger internal buffer, but Brave's
  API rejects the resulting `offset=20` request. The local page cap preserves
  all 20 candidates, avoids the redundant provider request, and keeps search
  logs clean.

## [0.92.2] - 2026-07-23

### Added

- The Cloud-Server Firecrawl deployment now has an optional, pinned SearXNG overlay backed exclusively by the Brave Search API. `bootstrap-vps.sh` enables it when a Brave key is configured and generates the separate SearXNG secret without printing it.
- `agent-doctor --strict` now performs a cached end-to-end Firecrawl search, so a reachable API with a broken search backend no longer passes the functional check.

### Changed

- Firecrawl research now requests up to 20 candidates in the first provider query, then scrapes and compares several relevant sources. The deterministic Linux and Windows wrappers use the same 20-result default.

## [0.92.1] - 2026-07-21

### Fixed

- Every generated Playwright MCP configuration now launches the same human-safe wrapper on Linux and Windows. When the wrapper attaches to an existing Chrome over CDP, disconnecting an MCP client detaches safely instead of closing the user's browser context.
- MCP endpoint placeholders are now materialized before rendering for Claude, Codex, and Antigravity. Node-based clients no longer receive a literal `${VAULT_LIBRARY_URL}` or tunnel-port placeholder, while authentication remains an environment reference and is never written into generated configuration. The doctor now probes the endpoint actually rendered from the manifest, catching an invalid hardcoded route even when the tunnel health check is green.
- The shared Chrome Playwright wrapper no longer replaces the browser's native Downloads directory with an MCP temporary-artifacts directory. Downloads initiated by an agent remain visible in the user's normal file manager after the MCP session ends.
- New cross-platform `agent-open-folder` launcher, generated by `agent-sync`, opens a validated absolute folder in the system file manager after an agent download.

## [0.92.0] - 2026-07-18

Onboarding for existing setups. A fresh machine was always easy; this release handles the common real case, a machine whose CLIs are already configured with their own MCP servers, skills, and old configs.

### Added

- **Read-only inventory.** `agent-sync inventory` (and `render.py --inventory`) reports, across every CLI, the MCP servers, skills, per-CLI bootstrap, and native memory it finds, split into canonical (in the manifest) and out-of-manifest strays.
- **Adopt.** `render.py --adopt <cli> --apply` promotes out-of-manifest MCP servers into the vault manifest, backing it up first and re-validating, so the canonical manifest is never left broken.
- **Reset.** `render.py --reset <cli>` backs up and removes a CLI's config so a fresh provision recreates it clean, reversible with `render.py --revert`, which now also restores a removed file.
- **Guided installer step.** `INIT.md` Step 1.5 (EN and IT) inventories an existing setup and offers a plain numbered menu: adopt what you have into the canonical source, start fresh, or pick item by item.
- **Doctor nudge.** `agent-doctor` gently warns (never fails) about skills materialized outside the manifest, pointing at the onboarding flow.

Claude native-memory confluence is an agent step in the installer (read the memory files, groom them, write via the memory MCP). Distilling the other CLIs' session transcripts is deferred to a later release. Maturity stays **Beta**.

## [0.91.4] - 2026-07-18

### Added

- `council allow-training on|off|status`: a host-local, persistent toggle for the zero-retention gate. When on, Council seats without a verified zero-retention guarantee run without repeating `--allow-training-risk` on every call; the default (off) keeps the protection on. The preference lives per-machine under the Council state directory and never touches shared data. Both zero-retention block messages now point at the command, so enabling it is discoverable instead of a flag to memorize.

## [0.91.3] - 2026-07-18

The doctor no longer grades how you configure your own CLI permissions. Permission posture is a host-local choice, not product policy.

### Changed

- `agent-doctor` dropped the "Claude security posture" section: it no longer comments on `bypassPermissions`, a suppressed dangerous-mode prompt, or persistent allow rules. Those are legitimate host-local choices (an isolated sandbox, a deliberate autonomous workflow), and singling out Claude while every other CLI's bypass modes went unchecked was an inconsistency, the same "one maintainer's config as product policy" anti-pattern that 0.91.2 removed for local models.
- The "Claude authentication" check is now gated on Claude actually being used on the host (a layer-managed `~/.claude/settings.json`). A user who does not run Claude no longer gets a logged-out FAIL they cannot act on.

## [0.91.2] - 2026-07-17

Migration hardening for Windows hosts upgraded through older NeXgen layouts. Fresh installs were not affected by these accumulated-state problems.

### Fixed

- `agent-sync` normalizes OpenCode instruction paths and keeps exactly one canonical `AGENTS.md` entry. Windows slash variants no longer load the same bootstrap twice. OpenCode's current `~/.config/opencode` path wins when an older `%APPDATA%` config also exists.
- OpenCode model, provider, and agent profiles are explicitly host-local. The doctor no longer treats one maintainer's DeepSeek or Ollama choices as product policy, and it warns when a retired shared profile still exists in the Vault data plane.
- The optional Windows local-worker adapter is resolved from private Vault scripts instead of the public engine checkout. Stable `local-worker` and `local-agent` commands remain, while managed `gemma-*` compatibility aliases are retired without deleting user-owned scripts.
- The doctor reports Claude's unsafe `bypassPermissions` default, a suppressed dangerous-mode warning, and unmanaged persistent allow rules. It also detects duplicate OpenCode bootstrap entries before they become silent context waste.

## [0.91.1] - 2026-07-17

Windows reliability fixes found during the 0.91 cutover. This patch restores Unicode-safe commands, corrects engine-owned launcher targets after the engine and data split, and makes an expired Claude session visible in the doctor.

### Fixed

- Council now writes Codex prompts to stdin as UTF-8. Timeout cleanup terminates the full Windows process tree and retries short NTFS lock failures before removing an ephemeral session.
- `agent-skill` forces UTF-8 on stdout and stderr, so skill bodies containing Italian text or Unicode symbols print correctly on legacy Windows code pages.
- Generated `vault-push` and `vault-groom` launchers now target the consumer engine. They no longer point at incomplete frozen copies in the data-only Vault after cutover.
- `agent-doctor` checks `claude auth status` on both Windows and POSIX hosts. An installed but logged-out Claude CLI is now a failure with `claude auth login` as the recovery command.

## [0.91.0] - 2026-07-17

Link hygiene for the memory plane, end to end: a deterministic structural map of the vault (broken wikilinks, orphans, hubs) wired as discipline into the flows — grooming candidates, a write-time advisory on every memory write, an orientation tool for agents, a seventh starter command — rather than as another periodic check. The version jump (0.9 → 0.91) signals proximity to a stable line, not 91 intermediate releases. Maturity stays **Beta** (stability is not yet guaranteed).

### Added

- Item 21 tranche B on the bundled `vault-library` MCP server: every write result now carries an additive, advisory-only `unresolved_links` list — the wikilinks in the JUST-written content (create/append/whole-note/section) that resolve to nothing, with a relocation hint when the target simply moved. It never blocks a write: a deliberate forward link stays legitimate, but a typo dies the moment it is born. A new read-only `map_overview` tool provides the token-bounded structural compass for probe-first orientation (counts, top hubs, first broken links with hints, first orphans), with semantics identical to `vault-map.py`. Server `__version__` 0.3.0; CI container smoke extended (dead-link advisory + overview).
- `vault-map`: a deterministic, read-only, stdlib-only structural map of the vault's wikilink graph (`03-INFRA/scripts/vault-map.py`) reporting broken links (a renamed note leaves dead links behind), orphan notes, and hub notes. Resolution semantics mirror the vault-library MCP server (path, unique stem, note title) so the map and the MCP never contradict each other; targets under `99-SECRETS` or existing non-markdown assets are valid-but-excluded, never "broken"; links from generated indexes are ignored for orphan/hub purposes but still checked for brokenness. Wired proactively into the flows rather than left as a periodic check: the `vault-groom` propose pass (both twins) now runs the map and treats orphans and broken links as first-class tranche candidates, `agent-doctor` (both twins) gains a WARN-only backstop line, and a seventh starter command skill `vault-map` explains the map in plain language and proposes fixes without applying them.

## [0.9.0] - 2026-07-17

Section-level memory editing: the bundled `vault-library` MCP server gains surgical per-section writes under a per-section compare-and-swap, shrinking diffs and the concurrent-write collision window. Maturity stays **Beta** (stability is not yet guaranteed).

### Added

- Section-level editing in the bundled `vault-library` MCP server: a new `update_section` write tool replaces exactly one ATX-heading section of a note under a **per-section** compare-and-swap hash, and `read_note` now returns an additive `sections` list (heading, level, per-section hash) to drive it. Compared to whole-note `update_note` — which stays untouched — the diff shrinks to the addressed section and a concurrent edit landed in a *different* section of the same note no longer invalidates the write. Fail-closed by design: unknown headings error out listing the available ones, duplicated/ambiguous headings are refused (fall back to `update_note`), heading-looking lines inside fenced code blocks are never boundaries, replacements must keep the section's heading level and cannot inject same-or-shallower headings that would reshape the note, and truncated (oversize) reads expose no section hashes at all. Covered by new behavioral tests against a real Git-backed vault plus an extended CI container smoke (edit one section, verify the sibling survives byte-identical, stale hash refused).

## [0.8.0] - 2026-07-17

One canonical skill now surfaces as an explicitly invocable command on every supported runtime ("write a command once, invoke it on all four CLIs"), plus six starter command skills. Maturity stays **Beta** (stability is not yet guaranteed).

### Added

- Cross-CLI command skills: one canonical skill can now surface as an explicitly invocable command on every supported runtime. `skills-sync.py` gains a native `antigravity` runtime view (`~/.gemini/antigravity-cli/skills/`, where a skill appears as a `/name` slash command in the agy TUI) and an `opencode` target that writes nothing but verifies the skill is discoverable through the shared roots OpenCode reads (`~/.agents/skills` and `~/.claude/skills`); Codex keeps discovering `exposure: core` skills through `~/.agents/skills` (`$name` mention). Grounded in a primary-source verification of all four CLIs (2026-07-17): Codex removed classic custom prompts in March 2026, Claude Code merged commands into skills, Antigravity uses markdown skills (not Gemini's TOML commands), and OpenCode surfaces discovered skills as slash commands — every runtime converges on the agentskills.io shape, so a skill is now the one portable command format. A WARN (never a failure) flags manifest names outside the portable lowercase-hyphen shape.
- Six starter command skills ship with the engine and are registered in `skills.manifest.yaml.example`: `vault-doctor` (run the read-only alignment doctor and explain the result in plain language), `vault-close` (distill the session into durable Vault notes, publish, verify), `vault-save` (save one durable fact with the hygiene decision rule), `vault-council` (convene the AI Council, confirming first because a run spends the seat CLIs' own quota; inert without `seats.yaml`), `vault-groom` (one grooming pass, preview-first and read-only by default; `apply` keeps its typed-yes + throwaway-clone guardrails), `vault-update` (check for a newer engine release, summarize the CHANGELOG in between, upgrade only on explicit confirmation via merge — never a bare checkout — then verify with the doctor and offer rollback on new FAILs). Bodies are argument-free by design — the text after the command is the request — because placeholder syntaxes diverge per CLI; each body encodes the documented runbook of the tool it wraps, so the guarded flows stay guarded.

## [0.7.0] - 2026-07-17

New engine tooling on top of the 0.6.0 Beta: per-CLI config `--revert` and `--adopt`, bootstrap-hygiene checks, and a required-invariant-rules drift guard. Maturity stays **Beta** (stability is not yet guaranteed).

### Added

- `agent-doctor` gains two read-only, warning-only bootstrap-hygiene checks (both the `.sh` and `.ps1` twins): a size budget for the canonical `AGENTS.md` bootstrap and for each `03-INFRA/*.md` detail note (overridable via `NEXGEN_BOOTSTRAP_MAX_BYTES` and `NEXGEN_NOTE_MAX_BYTES`), and a load-on-demand pointer-integrity check that flags a backtick-referenced vault note path that no longer resolves. Both only ever WARN, so they never turn a passing doctor red; the literal `03-INFRA/<topic>.md` placeholder in the editing-discipline prose is skipped.
- `render.py --revert CLI` restores a CLI's native config from the most recent render.py backup (`<file>.bak-*`), backing up the current file first so the revert is itself undoable. The restored backup is re-validated in its own JSON/TOML format before it is written, and nothing but that CLI's own config and its `.bak-*` siblings is touched.
- `render.py --adopt CLI` is a read-only onboarding helper: it lists the MCP servers present in a CLI's live config but absent from the manifest (the ones render already flags as OUTSIDE THE MANIFEST) and prints a DRAFT `manifest.yaml` entry for each to review and paste. It writes nothing; secrets are redacted to `<AUTH>` so a hand-added literal token is never echoed, while an env-var reference's name is kept (a name is not a secret).
- A required-invariant-rules drift guard: `03-INFRA/scripts/check_required_rules.py` verifies the canonical `AGENTS.md` still contains every non-negotiable rule signature declared in `agent-universal-layer/instructions/required-rules.txt` (prompt-injection defence, the `99-SECRETS` retrieval exclusion, single-source/no-hand-edit-a-derivative, compare-and-swap writes, vault-probe-first, push discipline). CI fails if the shipped bootstrap loses one; `agent-doctor` (both twins) WARNs if the running vault's bootstrap is missing one. Closes the failure class where a security or behaviour rule silently drops out of one copy of the instructions.

### Security

- Removed the private maintainer publication wrapper from product code.
  Public release enforcement belongs in GitHub pull-request controls, CI, and
  signed releases, while private tooling remains outside the repository.

## [0.6.0] - 2026-07-16

The project moves from Alpha to Beta: it runs in real use with known limits, and stability is not yet guaranteed.

### Changed

- Maturity label is now **Beta** across the README, `install.sh`, `install.ps1`, `CONTRIBUTING.md`, and `docs/council.md`; `VERSION` is bumped to `0.6.0`.
- The unassisted Windows cold install is reclassified from a Beta gate to a GA/1.0 onboarding gate. Rationale: a cold install by an unrelated first-time user tests first-install UX, a General Availability concern; Beta does not promise a polished unattended first install. The earlier entries below that call it "required for Beta" describe the release-gating policy at the time of those releases and are left unchanged.
- Removed the legacy "Agent-OS" product name and the "AgentOps governance" phrasing from first-run banners and onboarding files (`install.sh`, `install.ps1`, `INIT.md`, `AI-INSTALLER.md`, `AI-UNINSTALL.md`, `COMMERCIAL.md`, `CREDITS.md`), for consistent NeXgen Engine naming.

### Known limitations

- The unassisted Windows cold install (no maintainer present to diagnose failures) has not been run.
- Council `claude` and `ollama` seats are not yet verified live end to end; `agy` is refused as a passive seat (see `docs/council.md`).
- The 2026-07-15 Windows P1 fixes ship in code and are green in `windows-latest` CI, but the doctor-launcher and publish paths have not been re-verified on the physical Windows where the issues were originally found; `windows-latest` CI does not reproduce that condition.

## [0.5.6] - 2026-07-16

Windows command-launcher follow-up. This release remains Alpha.

### Fixed

- Windows commands under `~/.local/bin` are real PowerShell shims that invoke the engine script by absolute path. They no longer use file symlinks that change `$PSScriptRoot` and make launchers search for sibling files in the commands directory.
- `agent-sync.ps1` and `agent-doctor.ps1` select a Python 3 runtime only after it successfully imports PyYAML, following the installer preference order before falling back to the Windows `py -3` launcher.
- The doctor reuses that validated runtime for remote policy, MCP rendering, strict consumer checks, and skill coverage, instead of letting different checks silently select different Python installations.

Release gate: real Windows launcher and strict-doctor probes, full pytest
suite, public leak scan, GitHub Actions, signed commits and signed tag.

## [0.5.5] - 2026-07-16

Windows convergence follow-up. This release remains Alpha.

### Fixed

- An explicit `retired_servers` manifest list now removes deliberately retired MCP entries from all generated CLI dialects while preserving unknown live connectors by default.
- Antigravity HTTP MCPs now use an engine-owned Node bridge that keeps bearer values in the child environment and avoids the Windows argument-spacing bug that hid authenticated remote servers.
- `agent-sync` renders Antigravity's source before propagating it, so no-symlink Windows installs cannot copy a stale generation.
- The Windows doctor reads the target content of Antigravity symlinks instead of treating their zero-length link metadata as an empty config.

Release gate: full Windows pytest suite, public leak scan, live Antigravity
consumer probe, GitHub Actions, signed commit and signed tag.

## [0.5.4] - 2026-07-16

Windows host-safety hotfix. This release remains Alpha: the independent,
unassisted Windows cold-install gate required for Beta is still open.

### Fixed (Windows)

- Test and sandbox runs now cross an explicit no-host-mutations boundary before touching `HKCU\\Environment\\Path` or Task Scheduler. A real-registry invariant test prevents the pytest-path contamination that could push `PATH` past `cmd.exe`'s 8,191-character inherited-variable limit and break every npm-backed MCP at once.
- `agent-sync` refuses to grow a projected process `PATH` beyond that limit. `agent-doctor.ps1` checks user, combined registry, and current-process lengths and resolves engine-owned renderer and skill helpers from the engine checkout after an engine/data split.
- Generated scheduled-task wrappers live in per-user runtime state instead of the public checkout and preserve engine, vault-data, branch, and KnowledgeVault paths when the hidden guard starts.
- MCP rendering resolves Windows launchers to absolute commands, supplies a bounded Node-safe `PATH`, accepts `KNOWLEDGE_VAULT_PATH` as the data-root fallback, and rejects Codex aliases that collide after hyphen-to-underscore normalization.
- Playwright uses a Windows-only, fail-closed launcher that invokes npm through `node.exe` and `npm-cli.js`, avoiding Node 24's `spawnSync("npm.cmd")` failure while keeping Linux and macOS on the unchanged pinned `npx` path.
- Playwright remains mounted on every CLI. The generic Filesystem server stays scoped to the two product roots and scratch Memory remains explicitly opt-in; Google Calendar remains on demand.
- The Windows doctor now reports legacy skill folders awaiting explicit quarantine. The migration removes NTFS Junction views safely, Codex uses one official discovery root, and its documentation reflects native progressive disclosure correctly.
- Added a temporary PowerShell maintainer release gate, later moved out of
  product code.

Release gate: full Windows pytest suite, public leak scan, real MCP
`initialize` probes, GitHub Actions, signed commit and signed tag.

## [0.5.3] - 2026-07-15

Secondo pass di compatibilità Windows, con copertura dei percorsi nativi, dei wrapper CLI e delle scritture concorrenti su NTFS.

### Fixed (Windows)

- I processi CLI installati via npm vengono rilevati anche quando girano dentro `node.exe`, e gli shim `.cmd`/`.bat` vengono invocati tramite `cmd.exe` in Council e nei probe di routing.
- I percorsi OpenCode usano `%APPDATA%`, il Council usa `%LOCALAPPDATA%` per le sessioni, e le scritture atomiche ritentano in caso di `PermissionError` temporaneo.
- `agent-sync` quota correttamente i percorsi delle Junction, supporta i traduttori `alert-translate.ps1` e installa il wrapper PowerShell nativo di `firecrawl-local`.
- Il renderer MCP normalizza `npx`/`node`/`python3` nei nomi Windows nativi, preserva i campi extra aggiunti dai client live e non trasforma più un overlay runtime Codex in un falso drift fatale.
- Aggiunti il runner pytest `tests/run.ps1`, la policy `.gitattributes` per i file PowerShell e la documentazione con i comandi equivalenti Windows.

Verifica: `477 passed, 98 skipped` sulla suite `03-INFRA/agent-universal-layer/tests` eseguita su Windows con Python 3.14.

## [0.5.2] - 2026-07-15

Fix robustezza per il porting Windows (isolamento environment e junction NTFS).

### Fixed (Windows)

- Risolto `WinError 2` su Windows durante il subprocess call per `agent_sync.py publish`. Ora la chiamata sfrutta il fallback robusto su `shutil.which` ignorando path fittizi imposti durante l'isolamento dei test (fake `$USERPROFILE`).
- Risolto `WinError 5` su Windows per la mancata eliminazione (access denied) degli oggetti git read-only dentro `.git/objects` durante il `vault_groom_audit.py` aggiungendo un check robusto sui flag di read/write file attributes.
- Introdotto corretto unlinking per le Junction directory NTFS in `skills-sync.py`, aggirando i fallimenti generati da `shutil.rmtree` quando incontra i reparse point di Windows.

## [0.5.1] - 2026-07-15

A live-verification and correctness pass, one day after 0.5.0's first real
client install. Running the engine's own AI Council against itself —
prompted by a maintainer question about whether a multi-vendor relay
actually works end-to-end — surfaced a real seat-contract violation in the
Antigravity integration, now fixed. The same session live-verified all
four Council modes on opencode/codex (not just `challenge`, as before),
and corrected two doc claims that a real install had already outpaced:
Windows physical-verification status and the Cloud-Server local-mirror
write model.

### Added

- Community health files GitHub's Community Standards checklist was
  missing: `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `CONTRIBUTING.md`,
  issue templates (bug report / feature request), and a pull request
  template. English-only, matching `SECURITY.md`'s existing convention for
  contributor/maintainer-facing docs (as opposed to the bilingual
  installation-facing ones: `README.md`, `INIT.md`, `SUPPORT.md`).

### Fixed

- **`agy` (Antigravity) blocked as a passive Council seat.** A live 3-stage
  multi-vendor relay (opencode → codex → agy) found that `agy --print`
  ignores both `--model` and the given prompt, running its own "Context
  Initialization" that reads real files from the operator's home instead
  of answering — persistent state in fixed paths under `~/.gemini/`,
  resolved independent of `$HOME`, with no override flag or env var found
  to isolate it (checked live, including the installed binary's string
  table). Reproduced 5 independent ways. `run_seat` now refuses any
  `cli == "agy"` seat at the single point immediately before a process
  would be spawned — the authoritative check, not merely the earlier
  fail-fast checkpoints (`_check_seat_allowed`, the relay candidate loop),
  reviewed and confirmed necessary via `council challenge --seat
  codex-sol`. Does not affect using `agy` interactively as a *caller* of
  Council. Reactivation requires proving isolation, functional
  conformance, and a verifiable model identity — see `AGY_BLOCK_REASON` in
  `council.py` and `docs/council.md`.

### Changed

- **`brainstorm` and `code-review` verified live**, alongside the `agy`
  investigation above: `brainstorm` (opencode) produced a genuine
  self-attacking second round rather than a restatement; `code-review`
  (opencode) was run against a diff with a real, planted concurrency bug
  and correctly found it, unprompted. `docs/council.md`'s "Current
  limitations" previously only cited a 2026-07-13 live `challenge` for
  `codex`/`opencode` — it now cites live evidence for all four modes on
  those two CLIs. `claude` and `ollama` remain unverified live.
- **Windows platform status corrected to match reality.** README's "Platform
  status" section and a stale follow-up in `03-INFRA/vault-write-architecture.md`
  said physical Windows verification was still pending. It has since
  happened twice: a full guided MULTI install (three CLIs plus a
  Cloud-Server VPS deploy) and a separate realignment of an existing
  install to 0.5.0, both exercising `agent-sync apply`'s locked transaction
  for real, not just in CI. Docs now say exactly that — and name what's
  still open: a cold install with no maintainer present to catch failures.
  Deliberately not a jump to "released" or "Beta": that's the one gap left.
- **Cloud-Server local-mirror rule made explicit at install time**: a
  real physical Windows install surfaced that the installer agent never
  wrote or operated on the local Vault as read-only in Cloud-Server
  mode — the "notes only via MCP" rule existed (`AGENTS.md`,
  `03-INFRA/vault-write-architecture.md`), but only in files the
  install-time persona (`INIT.md`) isn't instructed to read, and
  `99-INDEX/USER-PROFILE.md`'s "If CLOUD-SERVER" section listed tunnel
  connection details without ever stating the write model itself.
  `INIT.md` (Step 4 and Step 7, both languages) now states the rule
  where the user actually chooses Cloud-Server and where the stack gets
  deployed, and instructs the installer to verify it is written
  explicitly into `USER-PROFILE.md` — the file every future session
  reads as its own deployment map — before closing the step.

## [0.5.0] - 2026-07-14

The pre-beta hardening round (2026-07-13) plus the end-user fixes from the
first real client install (2026-07-14): everything between the 0.4.0 tag
and this release. Fed by an independent second-model review of the whole
range, an external architecture challenge (Council relay to a real codex
seat), three implementation waves with adversarial re-review — and one day
of installing the engine at a paying user's site, which surfaced the two
gaps this release closes (vault-mcp not bundled, Firecrawl not
installable). The engine is running end-to-end on a physical Windows
machine as of this release.

### Added

- **Update alert on the default install**: `agent-doctor` (both twins) now
  warns when a released tag newer than the running `VERSION` exists, on the
  single-clone topology every `INIT.md` install actually produces — not
  just on the future split-clone one. Informational only (a warn, never a
  FAIL): upgrading stays deliberate per `docs/upgrade.md`. A pure data
  vault (no `VERSION` file, or an origin with no engine tags) skips the
  check silently.
- **The `vault-library` MCP server is now bundled and deployable**
  (`03-INFRA/deploy/vault-mcp/`): source, Dockerfile, compose (127.0.0.1
  binding, 512m cap, read-only container), and an idempotent
  `provision-vault-repo.sh` that stands up the vault bare repo + worktree +
  post-receive hook on the VPS. `bootstrap-vps.sh` deploys it as the fourth
  stack and auto-generates `VAULT_LIBRARY_TOKEN`. This closes the gap where
  Cloud-Server installs had no way to honor the "notes only via MCP" write
  model (the server's deployment source used to live outside the repo
  entirely) and installer agents fell back to raw git for notes.
  `AGENTS.md`'s push-discipline rule now states the door explicitly:
  vault-library down in Cloud-Server mode is an outage, never a license to
  commit notes with git.
- `vault-push` is cross-platform: its staging/commit/push logic moved into
  an `agent_sync.py vault-push` subcommand (same host-wide lock and
  subprocess timeouts as the rest of the control plane), with
  `vault-push.sh` and a new `vault-push.ps1` as thin launchers. When the
  engine itself is unreachable and `KNOWLEDGE_VAULT_REMOTE` is set, both
  launchers fall back to a minimal shell+git emergency lane, announcing
  the degraded mode loudly instead of failing.
- `install.ps1`: native Windows preflight twin of `install.sh` (`-Check`
  mirrors `--check`), so the documented first command works in a default
  PowerShell prompt.
- On Windows, `agent-sync apply` now registers the commands directory on
  the user PATH (idempotent, registry-type-preserving), and
  `agent-doctor.ps1` verifies it by actually resolving `agent-sync` in a
  fresh process. Before this, the linked bare commands were documented
  but unreachable on a fresh Windows install.
- `render.py --expected-servers <cli>`: the manifest-derived,
  env-filtered list of MCP servers a CLI should have. `agent-doctor
  --strict` (both twins) derives its expected set from it instead of a
  hardcoded 4-name list that permanently failed legitimate Local-Only
  installs.
- The vault gardener's write pass now runs inside a disposable clone of
  the vault with no remote configured — it physically cannot push,
  whatever the runner. A mechanical audit checks the produced commits in
  both directions (planned-but-missing AND touched-but-unplanned,
  path-exact, archive-rename aware) on a strictly linear history, and
  only a fully clean, still-fresh run is promoted — a fast-forward of the
  exact audited commit — into the real vault; anything else stays
  quarantined in the clone with the vault untouched. Runners: `claude`,
  `codex`, `agy` via `GROOM_RUNNER` (`opencode` refuses loudly, it has no
  per-invocation permission scoping today).
- First behavioral CI coverage for the PowerShell twins: `vault-groom.ps1`
  now runs for real under pwsh on both ubuntu and windows runners, and
  the vault-push contract tests (divergence/rebase recovery, mirror
  realignment, genuinely concurrent pushes) run on windows-latest too.
- A class-level invariant test: every project command documented as a
  bare command must be linked by the provisioner on every OS it claims —
  the bug class this round kept finding one instance at a time, closed as
  a class.
- Council: codex seats pass `--skip-git-repo-check` (the first real
  multi-vendor run died on codex's trusted-directory startup check);
  `council relay --continue-on-reject` to run the full stage sequence
  despite an intermediate rejection.
- Council: a non-blocking notice when an explicit `--seat` resolves to a
  `codex` seat whose model/effort no longer match Codex's own
  `config.toml` default — the call still goes through (forwarded
  explicitly with `-m`), but you're told your assumed default is stale
  instead of finding out some other way.
- Routing-resolver test coverage closes the gaps an audit found: the real
  `codex` `config.toml` probe (match, model mismatch, effort mismatch,
  missing/malformed config), the fixed `claude`-exclusion reason, the
  explicit zero-retention STOP path, every `RoutingContractError` branch in
  both the legacy table and JSON contract parsers, and
  `cmd_routing_status`'s blocked-role diagnostics.
- An import-ready n8n workflow
  (`03-INFRA/deploy/n8n/workflows/vault-grooming-reminder.json`) for the
  gardener's 14-day reminder. Reminder-only by design — the grooming pass
  itself stays on-demand, never self-scheduled.
- `03-INFRA/deploy/semantic-search-recipe.md`: a build specification for a
  `semantic_search` backend compatible with `vault-mcp`'s contract — exact
  embedding model, hybrid RRF ranking with title-boost, cross-encoder
  reranker, and resource footprint, precise enough for an AI coding agent
  to implement a compatible service from scratch. The backend's source
  still is not bundled (deliberate — see `README.md`'s "Shared services via
  MCP"); this closes the gap between "bring your own" and "no idea what
  'ours' actually looks like."

### Changed

- **Firecrawl deploy migrated to the current upstream architecture**
  (NUQ/Postgres, image line 2.11.x): the previous compose pinned the 2.11
  image but wired the retired API+worker+Redis-only shape, which
  crash-loops (missing `NUQ_DATABASE_URL`) — end users could not install
  Firecrawl at all. The stack is now api (upstream docker harness, runs the
  workers in-process), Redis (requirepass kept), RabbitMQ (pinned
  3.13.7-management, no host port), NUQ Postgres (upstream image pinned by
  digest, password auto-generated as `FIRECRAWL_POSTGRES_PASSWORD`, durable
  `firecrawl-nuq-data` volume now covered by `backup-restore.sh`), and
  Playwright (pinned by digest). Verified against upstream's compose at
  v2.11.91 and a production deployment of the same architecture. Budget
  note: the stack cap grew to ~6g total — see `03-INFRA/deploy/README.md`.
- `vault-groom` CLI contract: a bare run (or `preview`) is always
  read-only; the guarded propose → typed-yes → write lane is the explicit
  `vault-groom apply`. The interim `plan`/`run` arguments exit with a
  migration hint. The approved tranche's fingerprint is the sha256 of the
  plan-record file's raw bytes — identical on both OSes — and is
  re-checked immediately before the write pass runs.
- The gardener's push decision moved from an LLM instruction to
  deterministic code: promotion, the backlog record, and the publish all
  happen after the mechanical audit, never from inside the write pass.
- `council relay` stops on an intermediate `VERDICT: REJECT` by default,
  and verdict parsing is positional: only the response's last non-blank
  line counts (markdown emphasis and terminal punctuation tolerated,
  quoted verdicts ignored), so a cited rejection can no longer stop a
  relay and an inline-explained verdict still counts as one.
- `council`'s reasoning-effort forwarding and its display share one
  source per CLI: `--think` for ollama (with `xhigh`/`max` downmapped to
  `high`, labeled), `--variant` for opencode, and an explicit "(non
  applicato da questa CLI)" for agy.
- `docs/council.md` now matches actual behavior instead of a stale draft:
  `codex` seats' live-verification status (a `challenge` relayed to a real
  seat is verified; a full multi-vendor `relay` end-to-end is not, yet),
  the relay's default stop-on-`VERDICT: REJECT` and its
  `--continue-on-reject` escape hatch, the concrete `codex`
  `config.toml` probe rule with its exact diagnostic strings, why `claude`
  seats are excluded from the automated proposal but stay usable with an
  explicit `--seat`, and the positional VERDICT parsing / per-CLI
  reasoning-effort-forwarding rules.
- Every subprocess the sync control plane runs while holding the
  host-wide lock now has a timeout (`schtasks`, `systemctl`, `mklink`,
  `pgrep`/`tasklist`, `notify-send`) — the same hang class the 0.4.0
  round fixed for the render/skills subprocesses.
- `render.py` diff mode isolates a corrupted per-CLI config (whether it
  fails parsing or reading): the other CLIs still get diffed, the run
  still exits non-zero, and `agent-doctor` surfaces the actual STOP lines
  instead of a generic last-line summary.
- `skills-sync.py` warns on the aggregate size of Codex-targeted core
  skills, not just per-file, since several under-threshold skills can
  still defeat Codex's near-empty eager-scan discipline together.
- `bootstrap-vps.sh` derives the SSH ports to allow from sshd's real
  configuration (all of them), fixing the browser-console lockout case
  where `SSH_CONNECTION` is unset and sshd listens on a custom port.
- Shipped docs completed for a stranger on Windows: INIT/README document
  the native entry point and the new-terminal-after-first-apply step;
  AGENTS.md tells agents what to do when `vault-groom` is not yet linked;
  `offline-emergency-mode.md` no longer hardcodes the maintainer's own
  local model as universal fact.

### Fixed

- Firecrawl healthchecks were vacuous: `node -e "$HEALTHCHECK_JS"` let
  compose interpolate `$HEALTHCHECK_JS` at parse time (host env, unset →
  empty string), so the container actually ran `node -e ""` — always
  healthy, testing nothing. Caught during the live verification of the
  2.11 migration (`docker compose config` renders the empty string, plus a
  parse-time warning). Now `$$HEALTHCHECK_JS`, so the reference survives
  to the container shell and the probe really runs. Fixing that unmasked a
  second, stacked bug the vacuous check had been hiding: the TCP-connect
  probe read the socket from the `'connect'` callback argument, which Node
  passes as `undefined` — every real probe threw. The socket is now
  captured from `connect()`'s return value.
- `agent-sync`, `agent-doctor`, `vault-groom` and `firecrawl-local` were
  documented everywhere as bare PATH commands but never linked by any
  code path on any OS — including the systemd guard timer's own
  `ExecStart`, which depended on a symlink nothing created. All linked
  now, from a single source of truth consumed by both OS branches of the
  provisioner.
- `vault_groom_audit.py` invoked `python3` by name to publish — broken on
  stock Windows, where only `python` exists; it now uses the running
  interpreter.
- Three `Write-Error`-then-`exit` branches in `vault-groom.ps1` and one
  in `vault-push.ps1` could never reach their exit codes under
  `$ErrorActionPreference = 'Stop'`.
- `vault-groom.ps1` no longer passes prompts through argv shapes a
  cmd.exe shim can reparse (`|`, `<`, embedded newlines): runner commands
  resolve to their `.ps1`/executable form, with a byte-intactness test.
- One malformed name in `KNOWLEDGE_VAULT_MIRRORS` skips that mirror with
  a warning instead of failing the whole push, matching the pre-port
  behavior.
- Council's `codex` probe mismatch messages named neither the configured
  model/effort nor where to look; both now name the configured value, the
  seat's requested value, and the `config.toml` path they came from.
- Council's shutdown cleanup only had a `SIGTERM` handler: a `SIGINT`
  delivered solely to `council.py`'s own pid (a supervisor, a timeout
  manager, another agent interrupting just this process) left the vendor
  CLI child orphaned. `SIGINT` now runs through the same
  cleanup-then-re-raise path as `SIGTERM`.
- Kept from the earlier, pre-review cut of this section: `agent-doctor`'s
  "Tokens in env" check Mode-gated for `vault-library`; OpenCode's
  bootstrap-instructions pointer actually written; `bootstrap-vps.sh`
  sudo escalation on Oracle's default image; maintainer dogfooding purged
  from the shipped policy files; five resilience gaps in the sync control
  plane (lock-holding subprocess hangs, `vault-push.sh` not taking the
  lock, timer units written but never enabled, phases that could not
  report failure, non-UTF-8 alert config skipping the healthcheck);
  corrupted live config no longer reads as "CLI not installed"; the Codex
  known-bad-version check ported to Windows; stale `ghcr.io/mendableai`
  registry references; Windows CI skip patterns for two bash-only test
  files.

## [0.4.0] - 2026-07-13

A security-hardening and small-team-readiness pass: a dedicated audit found
28 issues across secrets handling, supply chain, injection surfaces, and
network exposure; every one confirmed by an independent adversarial check
before being fixed, and every fix verified against real CI, not just local
tests. Alongside it, the groundwork for evaluating this as shared
infrastructure for a small team, and the sync/skills work started earlier.

### Added

- Declarative team/organization routing: an optional `Team members` section
  in `USER-PROFILE.md`, per-member Council seat files, and a `personal`
  vs. `team` scope on skills. Explicitly a routing convenience, not access
  control. See `docs/team.md` and the new `docs/org-deployment.md`, which
  documents what a shared Cloud-Server backend does and does not protect
  for a small team today.
- Mode (Local-Only vs. Cloud-Server) is now a contract `agent-doctor` and
  `vault-push` actually verify, not just prose the LLM interprets. It stays
  a verified floor, never a ceiling: declaring Cloud-Server never blocks a
  connector you've configured anyway.
- Bearer-token authentication wired into n8n's MCP endpoint, the OCR API,
  and Firecrawl's Redis. None of these had a credential check before on a
  shared deploy.
- Host firewall baseline (`ufw`, idempotent) in the Cloud-Server bootstrap
  script.
- CI gates: ruff baseline, shellcheck, `pip-audit`, Docker Compose
  validation, and PowerShell static analysis. Previously only
  syntax/compile checks ran.
- `agent-skill list|find|show|path`, the small cross-platform command for
  loading exactly one managed skill body on demand.
- Explicit `exposure: manual|core` in the skill manifest, plus a generated
  safe catalog and a one-time `--migrate-legacy` quarantine for old eager
  folders.
- A data-owned `sync/remotes.yaml` policy, typed pull states, and a host-wide
  lock for the complete sync transaction.

### Changed

- Council seats for `codex`/`agy`/`opencode` now launch with an isolated,
  explicitly-allowlisted environment (and an isolated config directory
  where verified live) instead of inheriting the full host environment and
  every application token on it. Closes a path where a prompt-injected diff
  passed to `council code-review` could, in theory, reach a real MCP server
  despite the role prompt's text-only "no tools" instruction. The relay
  mode's output-redaction gate now runs in every Council mode, not only
  relay.
- The MCP manifest's npm-package pin check and its check against literal
  secrets in `env:` values now run on the manifest actually loaded at
  runtime (the user's vault copy), not only against a test fixture.
- Deploy image references pinned to explicit versions, with a Docker digest
  pin on the OCR image now that the leak-scan false positive below is
  fixed. GitHub Actions pinned to commit SHA instead of a mutable tag.
- CI workflow declares least-privilege `permissions: contents: read`.
- Managed skill bodies now live in `~/.agents/skill-library/`, outside eager
  discovery roots. Only explicitly core bodies enter `~/.agents/skills/` or
  Codex's runtime view. Claude retains declared native-lazy views.
- `agent-sync` normalizes unsafe whole-root links before materializing skill
  views, and `agent-doctor` verifies the library, catalog, and core exposure.
- `guard` and `apply` now regenerate runtime derivatives only after proving
  the vault fresh against its authoritative remote. Required phase failures
  are aggregated into a non-zero exit code. Publishing is a separate action,
  with configured mirrors downstream of the authoritative remote.
- Running `agent-sync` without arguments is help-only. The implicit combined
  `full` operation was removed.

### Fixed

- The anti-leak pattern for high-entropy secrets only matched a value
  wrapped in quotes: the same value unquoted (a bare `.env` line, an
  `Authorization: Bearer` header) passed both the CI gate and Council's
  always-on egress scan undetected.
- n8n backups were unencrypted and world-readable, and n8n's own encryption
  key was never set explicitly, so n8n generated one inside the same volume
  the backup archived. A copied backup exposed every credential n8n ever
  held. The documented GPG secrets workflow also decrypted to a
  world-readable temp file for the duration of every edit.
- Path traversal in skill names: an unvalidated manifest entry could write
  or symlink outside the intended skill library (confirmed with a live
  reproduction, not just static reading).
- Bearer tokens (`vault-library`, Firecrawl) were briefly visible on the
  process table via `curl`'s command-line arguments during a doctor probe
  or scrape call.
- `fastapi` bumped 0.115.6 → 0.139.0 (pulls a patched `starlette`), closing
  8 tracked CVEs in the OCR service's dependency chain. Validated with a
  real dependency-resolution check and a live RapidOCR round-trip rather
  than applied blind.
- A dependency-audit exception scoped to the OCR service's known debt used
  to apply to every `requirements*.txt` in the repo, not just that one file.
- Legacy migration preserves declared Claude native-lazy links instead of
  treating them as stale eager copies.
- Dirty, wrong-branch, ahead, diverged, missing-remote, fetch-failed, and
  malformed-manifest states can no longer degrade into a successful-looking
  propagation run.
- The distributed MCP manifest's `filesystem` server no longer mounts the
  user's entire home (a bare `${HOME}` argument). It now mounts two
  explicit, configurable roots: `AGENT_ENGINE_ROOT` and `AGENT_VAULT_DATA`
  (the same canonical engine/data roots the rest of the layer already
  resolves). A user can add more roots as extra `args` entries. The
  `memory` server is no longer mounted by default: it required
  `MCP_MEMORY_OPT_IN` because it is a second, non-authoritative memory
  channel outside the KnowledgeVault.

## [0.3.2] - 2026-07-10

### Fixed

- Windows CI no longer applies POSIX mode-bit assertions to NTFS files.
  The test still verifies that the generated configuration and backup exist;
  owner-only mode checks remain enforced on POSIX, where they are meaningful.

## [0.3.1] - 2026-07-10

### Fixed

- Windows runtime skill directories backed by Junctions now recover safely.
  The provisioner recognizes directory reparse points even on Python builds
  without `Path.is_junction()`, removes a whole-hub loop through the shared
  path adapter, and preserves per-skill Junctions already pointing at their
  hub source instead of recursing into them.

## [0.3.0] - 2026-07-09

### Added

- `AI-INSTALLER.md` / `AI-UNINSTALL.md`: fast, autonomous companions to
  `INIT.md` / `docs/uninstall.md` for an agent to run with minimal
  back-and-forth. Both defer to the existing guide for the actual
  mechanism (no duplicated/divergent instructions) and require explicit
  confirmation before any destructive step.
- `agent-doctor`: a short, pruneable "third-party CLI compatibility" check
  that flags a known-broken Codex CLI release (a real tool-dispatcher
  regression, not a general version pin) instead of failing silently or
  mysteriously when every tool call gets rejected.

## [0.2.0] - 2026-07-09

### Added

- Anti-leak protections, including local hooks and CI leak-scan, guarding
  public changes: a single blocked finding stops the release path.
- Regression test suite (`tests/run.sh`, 40 pytest cases) covering render.py,
  the provisioner, skills-sync.py and agent-doctor.sh in a sandboxed HOME.
- `agent_sync.py`: single cross-platform provisioner replacing the old
  `agent-sync.sh` / `agent-sync.ps1` duplication. The `.sh`/`.ps1` files are
  now 5-line launchers; same CLI, same exit codes, same log file.
- CI job `engine-tests-windows` (pytest on `windows-latest`), so Windows
  coverage no longer depends on physical access to a Windows machine.
- Consumer engine clone version-pin check in `agent-doctor` (S2): flags
  silent drift between the pinned commit and what is actually checked out.
- Data-schema migration framework (`data_migrations()` in `agent_sync.py`):
  versioned, idempotent, backs up affected files before writing. No
  migrations are registered yet — today's data shape is the baseline.
- `VERSION` file and this changelog.
- Path-traversal guard in `skills-sync.py`'s GitHub-origin skill installer.
- Atomic writes (temp file + replace) for live config files the provisioner
  regenerates on every run (`settings.json`, `CLAUDE.md`, the systemd unit,
  generated MCP configs).

### Changed

- All engine strings are English-only. Localizing alerts is a user-data
  concern: the engine calls an optional translator script if the vault
  provides one, falling back silently to English otherwise.
- The systemd timer persists `AGENT_ENGINE_ROOT`/`AGENT_VAULT_DATA` across a
  cutover instead of reverting to the default layout on the next run.
- Personal instance data (the user's own `AGENTS.md`, MCP manifest) is
  always resolved from the data root, never from wherever the engine happens
  to be installed.

### Fixed

- Several engine/data path-resolution bugs where a script silently fell back
  to reading the personal data copy instead of the installed engine after a
  cutover (`agent-doctor`, `skills-sync.py`, the provisioner itself).
- Fresh install with no skills manifest yet: `skills-sync.py` no longer
  crashes, and `agent-doctor`'s skill check no longer hardcodes anyone's
  personal skill names — zero configured skills is a warning, not a
  permanent failure.
- OCR MCP server: read-before-size-check memory exhaustion, double file
  read, and unsanitized multipart filename header injection.
- Symlink race (CWE-59) in a script's temp-file handling.
- A lifecycle-audit script silently auditing the wrong directory when run
  from the engine clone instead of the data root.
- Restored an executable bit lost since the first public release.

### Removed

- `agent-healthcheck.sh`: dead code, fully superseded by `agent_sync.py`'s
  built-in healthcheck step.

## [0.1.0] - 2026-07-07

Initial public release: repositioned as an AgentOps control layer, hardened
the public trust surface, calibrated the README's claims against what the
engine actually does today.
