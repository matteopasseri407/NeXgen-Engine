# Overlap Map: NeXgen Engine and OpenClaw

This document defines the architectural boundary between NeXgen Engine and the OpenClaw harness (2026.10.6), cataloging overlapping capabilities and recording decisions to **retire**, **delegate**, or **keep**.

## Architectural Boundary

OpenClaw is the **harness** for user interaction and agent execution. NeXgen Engine retains **exclusively**:
- **Council**: independent multi-model peer review and advice.
- **Supervision (`sup`)**: task harness with independent verification and gatekeeping.
- **The cage**: security guardrails, authorization posture, and the local model containment cage.
- **Model Governor**: routing based on subscription quota and availability, fallback chains.
- **Evidence**: factual verification of answers against real retrieval receipts.
- **Canonical library of skills and MCP servers**: maintenance of canonical sources, version pinning, depwatch, propagation.
- **The Vault and canonical memory**: durable persistence on version-controlled Markdown notes, CAS, hygiene, and push.
- **Third-party operational services**: management of containerized auxiliary services (n8n, vault-mcp, OCR).
- **Engine update channels**: self-verification and updates of engine code.
- **Cross-CLI and cross-machine synchronization**: fail-closed rendering and propagation across Linux and Windows for all supported CLIs (Claude Code, Codex, Antigravity, OpenCode, OpenClaw).

Everything else belongs to OpenClaw directly:
- Graphical web interface, desktop app, and mobile/phone app
- Agent execution, interactive loops, and prompt dispatch
- Workboard
- Messaging channels (Telegram, WhatsApp, Discord, Slack, Matrix, etc.)
- Session memory, transcripts, semantic search, and dreaming
- Skill Workshop
- Cron, scheduler, and planned automations
- Distributed node pairing and management
- Voice and speech synthesis (TTS/STT)
- Detailed usage, token, and cost tracking
- Tool search and execution
- Interactive approvals

Overlapping engine code that duplicates capabilities covered by OpenClaw is retired or delegated.

---

## Overlap Inventory

| Engine Component | What the Engine Does | OpenClaw Feature (Installed Documentation) | Verdict | Rationale |
|---|---|---|---|---|
| `nexgen shell` / `repl` / `interactive`<br>`03-INFRA/scripts/nexgen_core/tools/shell.py`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | Interactive terminal REPL (`cmd.Cmd`) with numbered menu to run engine verbs. | `openclaw tui` (`openclaw/docs/cli/tui.md`, `openclaw/docs/web/tui.md`), Web Dashboard (`openclaw/docs/cli/dashboard.md`), Control UI (`openclaw/docs/web/control-ui.md`). | **retire** | OpenClaw provides full interactive interfaces (TUI and Control UI). The engine REPL is redundant duplicate code and has been removed entirely. |
| `nexgen info -i` / `--interactive`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | Launches interactive shell after displaying status. | `openclaw tui` (`openclaw/docs/cli/tui.md`), Control UI (`openclaw/docs/web/control-ui.md`). | **retire** | `--interactive` previously launched `nexgen shell`. It now prints a direct guidance line pointing to OpenClaw TUI. |
| `nexgen info` / `status`<br>`03-INFRA/scripts/nexgen_core/tools/info.py`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | Textual dashboard showing cross-CLI alignment, sync state, and memory health. | `openclaw status` (`openclaw/docs/cli/status.md`), Dashboard (`openclaw/docs/web/dashboard.md`). | **keep** | `nexgen info` monitors the multi-CLI engine ecosystem across all CLIs (Claude, Codex, Antigravity, OpenCode, OpenClaw). OpenClaw inspects only itself. |
| `nexgen-local run <question>`<br>`03-INFRA/scripts/nexgen_local/cli.py`<br>`03-INFRA/scripts/nexgen_local/cmds/run.py` | Autonomous query-answering loop with local models. | `openclaw agent` (`openclaw/docs/cli/agent.md`), runtime execution (`openclaw/docs/openclaw-agent-runtime.md`), chat TUI/web. | **retire** | OpenClaw is the designated harness for chatting with agents and models. `nexgen-local run` has been retired in favor of `openclaw agent` / `openclaw tui`; `run_lane` remains the internal evaluation engine for `eval` and `tier evaluate`. |
| Local cage connectors (`calendar.py`, `drive.py`, `gmail.py`, `outlook.py`, `web.py`)<br>`03-INFRA/scripts/nexgen_local/connectors/` | Embedded connectors used by local models inside the containment cage. | Standard MCP servers (`openclaw/docs/cli/mcp.md`) and browser tools (`openclaw/docs/tools/browser/agent-tools.md`). | **keep** | Connectors in `nexgen_local/connectors/` are internal tools for the local model containment cage (`nexgen_local/tools.py`), not an OpenClaw duplicate. Any future consolidation is an internal engine matter. |
| Drive upload & MCP (`drive-propose`, `drive-upload`, `drive-mcp`)<br>`03-INFRA/scripts/nexgen_local/cmds/drive.py`<br>`03-INFRA/scripts/nexgen_local/drive_mcp.py` | Staging, upload, and stdio MCP server for Google Drive. | Native file transfer (`openclaw/docs/cli/file-transfer.md`) or Google Workspace MCP. | **keep** | `drive-mcp` is an engine MCP server distributed by the canonical library across all runtimes, not an OpenClaw feature. |
| Guardian timers and heartbeat (`agent-sync.timer`, `agent-heartbeat.timer`)<br>`03-INFRA/scripts/nexgen_core/scheduler.py` | Registers systemd / Scheduled Tasks timers for fail-closed sync and engine liveness. | `openclaw cron` / `automations` (`openclaw/docs/cli/cron.md`, `openclaw/docs/automation/cron-jobs.md`). | **keep** | Engine timers maintain machine convergence and guardian liveness even if OpenClaw is stopped or not installed. |
| User application cron and schedules | No user cron scheduling in engine. | `openclaw cron` (`openclaw/docs/cli/cron.md`, `openclaw/docs/automation/cron-jobs/managing-jobs.md`). | **delegate** | User scheduled automations and periodic tasks belong entirely to OpenClaw's cron subsystem. |
| Notifications and alerts (`Megaphone`)<br>`03-INFRA/scripts/nexgen_core/megaphone.py`<br>`nexgen notify-failure`<br>`nexgen bootstrap-alerts` | Operational alerts for engine failures (guardians, doctor). Dispatches via OpenClaw CLI first, with direct fallback transports. | `openclaw message send` (`openclaw/docs/cli/message.md`), Channel Routing (`openclaw/docs/channels/channel-routing.md`). | **keep (fallback transport)** | When the OpenClaw gateway is on another machine or unreachable, a host must still deliver critical alerts. `megaphone.py` already uses OpenClaw first and keeps direct transports as emergency fallback. |
| Session briefing (`briefing.mjs`, `vault-briefing.txt`)<br>`03-INFRA/agent-universal-layer/hooks/openclaw-guardrail-plugin/briefing.mjs` | Injects essential Vault context at session start and after compaction. | Session start hooks, bootstrap prompts, context compaction (`openclaw/docs/openclaw-agent-runtime.md`). | **keep** | Briefings enforce canonical Vault rules and context across CLIs; implemented as a plugin hook in the harness. |
| Session continuity checkpoint (`checkpoint.mjs`)<br>`03-INFRA/agent-universal-layer/hooks/openclaw-guardrail-plugin/checkpoint.mjs` | Writes `openclaw-checkpoint.json` at session close so Claude Code can resume context. | Session management (`openclaw/docs/cli/sessions.md`), transcripts (`openclaw/docs/cli/transcripts.md`). | **keep / delegate** | Session state is managed by OpenClaw; the bridge checkpoint exists solely for cross-runtime handoff to Claude Code. |
| Persistent and semantic memory (`nexgen vault *`)<br>`03-INFRA/scripts/nexgen_core/cli/vault_cmds.py` | Manages the KnowledgeVault (push, map, groom, lifecycle). | `openclaw memory` (`openclaw/docs/cli/memory.md`, `openclaw/docs/concepts/memory.md`). | **keep** | The Vault is the authoritative persistent memory on Git. OpenClaw handles session memory and dreaming, while the Vault remains the anchor of truth. |
| Agent model and auth profile configuration | Generates baseline in `openclaw.json` (`profile.py`, `setup_agents.py`). | `openclaw models` (`openclaw/docs/cli/models.md`), `openclaw models auth`, `openclaw models scan`, `openclaw secrets`. | **delegate** | Live model configuration, scanning, and per-agent auth profiles belong to native `openclaw models` commands. |
| Model Governor and quota fallback (`governor.py`, `governor.mjs`)<br>`03-INFRA/scripts/nexgen_core/openclaw/governor.py` | Computes and applies fallback chains to protect flat subscriptions and prevent overages. | Static model fallbacks in `openclaw.json` (`openclaw/docs/concepts/models.md`). | **keep** | Explicit engine domain: model governor (routing, subscription quota, fallback). |
| Subscription quotas (`nexgen quota`)<br>`03-INFRA/scripts/nexgen_core/quota.py` | Reads utilization and reset windows for flat subscriptions (Claude, OpenAI, Codex, Antigravity) to feed Governor. | Provider usage windows (`openclaw/docs/concepts/usage-tracking.md`). | **keep** | Feeds the Governor to determine fallback chains deterministically without spending paid tokens. |
| General session token and cost tracking | `nexgen-local sup costi` tracks only supervised unit costs. | `/usage cost`, `/status`, `openclaw status --usage` (`openclaw/docs/concepts/usage-tracking.md`, `openclaw/docs/reference/api-usage-costs.md`). | **delegate** | Broad monitoring of tokens, costs, spend per provider, and trends belongs to OpenClaw. |
| Canonical skill management (`nexgen skill *`)<br>`03-INFRA/scripts/nexgen_core/cli/skill_cmds.py`<br>`03-INFRA/scripts/nexgen_core/skills.py` | Cross-platform canonical catalog (`skills.manifest.yaml`), pin verification, depwatch, propagation. | `openclaw skills` (`openclaw/docs/cli/skills.md`), Skill Workshop (`openclaw/docs/tools/skill-workshop/personal-library.md`). | **keep** | The canonical library is an engine responsibility. OpenClaw creates skills interactively in Skill Workshop; the engine adopts them. |
| Skill adoption from Skill Workshop (`nexgen openclaw adopt --skills`)<br>`03-INFRA/scripts/nexgen_core/openclaw/adopt.py`<br>`workshop.mjs` | Adopts skills created in OpenClaw Skill Workshop into the canonical catalog. | Skill Workshop (`openclaw/docs/tools/skill-workshop/personal-library.md`). | **delegate / keep** | OpenClaw provides the authoring environment; the engine promotes adopted skills to all other runtimes. |
| Local model measurement and cage (`nexgen-local eval`, `nexgen-local tier`)<br>`03-INFRA/scripts/nexgen_local/cmds/run.py`<br>`03-INFRA/scripts/nexgen_local/cmds/tier.py` | Runs adversarial trap suites (prompt injection, confabulation) to assign containment tiers. | No adversarial containment suites exist in OpenClaw. | **keep** | Core engine domain: the cage (guardrails, posture, local model cage) and evidence verification. |
| Doctor and system diagnostics (`nexgen doctor`)<br>`03-INFRA/scripts/nexgen_core/doctor.py` | Verifies cross-CLI alignment, git remotes, MCP drift, invariant rules. | `openclaw doctor` (`openclaw/docs/cli/doctor.md`). | **keep** | Each doctor validates its domain: `openclaw doctor` checks the OpenClaw installation; `nexgen doctor` verifies convergence of the entire multi-CLI ecosystem. |
| Node and device pairing (`nexgen openclaw pair`, `node`)<br>`03-INFRA/scripts/nexgen_core/cli/openclaw_cmds.py` | Ergonomic wrapper commands to pair nodes and phones. | `openclaw nodes` (`openclaw/docs/cli/nodes.md`), `openclaw pairing` (`openclaw/docs/cli/pairing.md`), `openclaw devices` (`openclaw/docs/cli/devices.md`). | **delegate** | The engine provides an ergonomic CLI shortcut, while device registration and cryptographic pairing belong to OpenClaw. |
| Engine software updates (`nexgen update`)<br>`03-INFRA/scripts/nexgen_core/updater.py` | Updates NeXgen Engine from source repository or release. | `openclaw update` (`openclaw/docs/cli/update.md`). | **keep** | Each updater upgrades its respective codebase. |

---

## Operating decisions for the local agy backend

`agy` runs in two places at once: the supervision lane starts it directly, and OpenClaw
starts the same CLI through the `google-antigravity-cli` CLI backend plugin.
The two must hand it the same posture, the same tools and the same model list.

| Fact | Outside OpenClaw | Inside OpenClaw |
|---|---|---|
| Executor mode | `agy --mode accept-edits` | the plugin owns its own argv |
| Read-only round | `agy --mode plan` | one turn per message: no scout mode |
| Model | `agy --model <id>`, from `sup run --model` | the route `google-antigravity-cli/<id>` |
| MCP servers | `~/.gemini/antigravity-cli/mcp_config.json` | the same file: the plugin starts `agy` in the user's home |

### `permissionMode` is not `--mode`

The plugin declares three permission modes, and none of them is an execution mode.
`skip` is `agy`'s own self-approval (`--dangerously-skip-permissions`), `sandbox` turns on
the CLI's terminal sandbox (`--sandbox`), and `settings` leaves approval to `agy`'s settings
file.
`--mode accept-edits|auto|plan` is a different axis: it says whether the CLI may edit files,
not whether a person approves a tool call.

The setup therefore writes `permissionMode: "sandbox"`, keeps a guarded mode already in
force (`sandbox` or `settings`), and never writes `skip`.
`nexgen openclaw doctor` warns when it finds `skip`, or no mode at all, because `skip` is
the plugin's default.
The engine never enables or disables the plugin itself: it aligns the one setting inside an
entry the owner already approved.

One difference remains, and it is stated rather than hidden.
The supervision lane passes `--mode accept-edits` on the tab it opens, while the plugin
leaves the execution mode to its own argv, where the engine does not guess.
Both sides keep the same rule, `agy` never approves its own tool calls, and the mode the
engine can verify is the one it writes.

### One MCP file, with the gateway left out

`agy` reads `~/.gemini/antigravity-cli/mcp_config.json`, and the engine writes that file from
the canonical manifest (`~/.gemini/antigravity/mcp_config.json`, fanned out to the folders
Antigravity reads).

When OpenClaw drives the local `agy` backend, the antigravity renderer writes the servers
OpenClaw mounts directly and leaves `lazy-mcp` out: the gateway entry belongs to OpenClaw
and carries its CLI identity (`LAZY_MCP_CLI=openclaw`), which is not the one `agy` would
need, and `agy` reaches those connectors directly through this file. The plugin starts the
same CLI from the same home, so one file has to serve both lives.
This is the one dialect where a server declared `exposure: lazy` is not served by the
gateway: of the two contexts reading the file, the one that cannot use this gateway wins.

### The model picker on every agent

`model` on an agent is only its default.
`nexgen openclaw setup` registers one wildcard per reachable provider in
`agents.defaults.modelPolicy.allow`, and the squad registers each model it names, so every
agent's picker lists every model this machine can run, the local `agy` backend included.
A CLI runtime is a pin on the model, not a provider in that list: a Claude login gives
`anthropic/*` with `agents.defaults.models["anthropic/*"]` pinned to `claude-cli`, and
`agents.defaults.model` names the assistant's model as the system primary, so OpenClaw's
`doctor --fix` finds the shape it would have migrated to and changes nothing.
The rule and its reasons live in `nexgen_core/openclaw/model_policy.py`; what the owner sees
is in [Squad as data](squad.md#model-choice-per-agent).

---

## Retirements Executed in This Task

- **`nexgen shell` (and aliases `interactive`, `repl`)**:
  - Completely removed `03-INFRA/scripts/nexgen_core/tools/shell.py`.
  - In `03-INFRA/scripts/nexgen_core/cli/engine.py`, `nexgen shell` and `nexgen info -i` print a direct redirect notice:
    `The interactive shell has been retired: use 'openclaw tui' or the OpenClaw Dashboard instead.`
  - Removed obsolete internal REPL tests (`test_nexgen_toolshellsplit_pinning.py`), keeping redirection and parser tests in `test_nexgen_info_shell.py`.

- **`nexgen-local run <question>`**:
  - Retired the user-facing command. Invoking `nexgen-local run` outputs:
    `nexgen-local run è stato ritirato: usa 'openclaw agent' o 'openclaw tui' (l'esecuzione degli agenti appartiene a OpenClaw).`
  - Removed `run` from the subcommands list on `nexgen-local --help` (`EXPECTED_COMMANDS` in `test_nexgen_local_help_surface.py`) and recorded it in `RETIRED_COMMANDS`.
  - Kept `run_lane` in `nexgen_local/engine.py` as an internal driver for `eval` and `tier evaluate`.
