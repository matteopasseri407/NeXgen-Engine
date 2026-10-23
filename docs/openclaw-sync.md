# OpenClaw sync

What `nexgen sync` (historically `agent-sync apply`) puts into OpenClaw, where it
puts it, and what it deliberately leaves alone. This page is the engine-side view
only: it lists what the sync writes, not how to run the harness.

OpenClaw resolves its configuration in this order, and the engine follows the same
one (`nexgen_core.paths.openclaw_config`):

1. `$OPENCLAW_CONFIG_PATH` — a file.
2. `$OPENCLAW_STATE_DIR/openclaw.json`.
3. `~/.openclaw/openclaw.json`.

The **state directory** is the folder that file lives in: `~/.openclaw` by
default, after the other two when they are set. Everything below is relative to
it.

| What | Where | Written by |
|---|---|---|
| MCP connectors | `mcp.servers` in `openclaw.json` | the MCP renderer |
| Instructions | an `AGENTS.md` block in each agent workspace | the guard's instructions phase |
| Skills | `~/.agents/skills/` (the shared catalog) | the skills phase |
| The plugin | `<state dir>/extensions/nexgen/` | the guard's permissions phase |
| Briefing, events, posture | `<state dir>/extensions/nexgen/nexgen.config.json` | the permissions phase and the module state |

## MCP connectors

The manifest's servers are written into the `mcp.servers` table of `openclaw.json`
and nothing else in that file is touched: models, channels, gateway settings and
servers the manifest does not declare are OpenClaw's own. A machine where OpenClaw
was never set up has no config file, and the renderer does not invent one.

In `mcp.servers.*.env`, every `${VAR}` stays a reference in `openclaw.json`:
OpenClaw substitutes it itself, and a credential must not be copied out of the
environment into a file. The exception is a value whose references are all
engine-owned paths or ports (`AGENT_VAULT_DATA`, `AGENT_ENGINE_ROOT`,
`KNOWLEDGE_VAULT_PATH`, the tunnel ports), which is written resolved because the
gateway's environment is not the engine's. A value that mixes the two stays a
reference: a missing port is a better outcome than a leaked secret.

## Instructions

OpenClaw has no pointer file. At every session it loads the `AGENTS.md` of the
agent's working directory, truncated at `agents.defaults.bootstrapMaxChars`
(20000 characters by default). The engine therefore keeps a delimited block at the
**top** of that file:

```markdown
<!-- nexgen:instructions:begin -->
...
<!-- nexgen:instructions:end -->
```

The block says where the canonical `AGENTS.md` of this machine is and to read and
follow it at session start. It carries no policy of its own, so the two cannot
drift apart. Everything outside the markers is yours and is never rewritten; a
file that does not exist is created with the block alone; a workspace folder that
does not exist is not created here (the setup command does that). Before a file
that already exists is changed, a `.pre-instructions-<timestamp>.bak` copy of it is
left beside it, the same convention the other instruction pointers use — an
OpenClaw workspace is often a Git repository, and that copy shows up there as an
untracked file until you delete it.

The working directories come from OpenClaw's own config:
`agents.entries.<id>.workspace` per agent, `agents.defaults.workspace` for the
agents that pin none, and `<state dir>/workspace` when neither exists.

## Skills

OpenClaw discovers `~/.agents/skills` by itself as its *personal agent skills*
source, for the agents on the default state directory. That folder is already the
shared catalog of the engine, so `openclaw` is a skill target with no folder of its
own: a manifest entry that names it in `targets` is materialized there, and one
that names another runtime only is not. `~/.agents/skills/INDEX.md` lists each
skill with its targets, so "does this skill reach OpenClaw?" is readable without
opening the manifest.

## The plugin: one plugin, three jobs

OpenClaw's hooks are JavaScript callbacks inside the gateway process, so the engine
ships one native plugin and deposits it, byte for byte, in
`<state dir>/extensions/nexgen/`:

```
extensions/nexgen/
  index.mjs                     registers the two jobs; the guardrail comes first
  guardrail.mjs                 the gate on before_tool_call
  briefing.mjs                  the vault briefing after a compaction
  engine-config.mjs             reads nexgen.config.json, once for both
  session.mjs                   which session a hook event belongs to
  package.json                  openclaw.extensions -> ./index.mjs
  openclaw.plugin.json          id: nexgen, onStartup
  nexgen-guardrail-core.mjs     the shared core every adapter imports
  nexgen-guardrail.config.json  which guardrail body, with what timeout, the audit file
  nexgen.config.json            the engine's switches: briefing, posture
  vault-briefing.txt            the briefing text, shared with Claude Code
  hooks/<body>.mjs              the guardrail body (the private policy)
```

Two things about enabling it:

- **The plugin is deposited, not enabled.** Editing `plugins.*` while the gateway
  runs breaks the gateway, so the engine never does it in the guard cycle.
  `nexgen openclaw setup` enables it with the gateway stopped. Until then
  `nexgen doctor` reports it as deposited and not enabled, which is the truth and
  not a failure of the sync.
- **One id.** The folder is `nexgen`; the old `nexgen-guardrail` folder is taken
  back on the first deposit, and only when its manifest proves it is the engine's
  own. A folder by that name that is not ours is never deleted.

### Guardrail

On `before_tool_call`, a tool call that carries a shell command (`exec`, `process`,
`terminal`, `code_execution`) is handed to the guardrail body in the same
stdin/stdout JSON that Claude Code's own `PreToolUse` hooks use. A denial blocks
the call with the body's reason; a question becomes an approval request for the
person; an allow leaves the call alone. A failure never becomes an allow: a missing
core, an unreadable sidecar or a body that crashes blocks, or asks, and OpenClaw
itself blocks the call when the handler throws.

### Briefing

OpenClaw's `after_compaction` and `session_start` are observations: they have no
place to return context, so the plugin marks the session there (a `session_start`
counts only when it carries `resumedFrom`) and injects on the first
`before_prompt_build` that follows, once and never again for that session. The text
is `vault-briefing.txt`, the same file Claude's `claude-vault-checkpoint.mjs`
injects: one text, two hosts.

The layer follows Claude's own fact: the briefing is switched on only when the
checkpoint hook is registered on `SessionStart` in `~/.claude/settings.json`. The
engine deploys no copy of that hook, so its registration is the observable
condition. A briefing that fails is swallowed: a session that loses its briefing is
a worse session, not a broken one.

### Posture

The private permission manifest's posture travels into `nexgen.config.json` in the
neutral vocabulary, and the plugin renders it there. `openclaw.json` is never
rewritten by the permissions phase, because it is read at gateway start and must
not change under a running one.

| Posture | What OpenClaw does |
|---|---|
| `bypass` | the guardrail decides alone: what the body permits runs without a question, what it denies is blocked, a body that asks still asks |
| `accept-edits` | like `bypass` for file writes; a shell command the body does not deny asks for approval |
| `ask` | every shell command asks for approval, and a tool that changes files (`write`, `edit`, `apply_patch`) asks too |

Without a posture in the manifest nothing is written, exactly like the other CLIs;
the plugin then behaves as `bypass`.

## Parity with Claude Code

The engine puts the same six layers in both hosts. `nexgen doctor` reports one
`openclaw.parity.*` outcome per layer and stays silent on a machine that does not
have both CLIs.

| Layer | Claude Code | OpenClaw | Runtime `claude-cli` / `codex` |
|---|---|---|---|
| MCP | `mcpServers` in `~/.claude.json` | `mcp.servers` in `openclaw.json` | Claude Code / Codex use their own rendered config |
| Instructions | `~/CLAUDE.md` points at the canonical `AGENTS.md` | the managed block in each workspace `AGENTS.md` | the agent's own runtime loads its instructions |
| Skills | `~/.claude/skills` | `~/.agents/skills` (shared catalog) | the agent's own runtime loads its skills |
| Guardrail | `PreToolUse` → `claude-guardrail-adapter.mjs` | `before_tool_call` in the plugin | Claude Code's / Codex's own guardrail, installed by this engine |
| Briefing | `SessionStart` → `claude-vault-checkpoint.mjs` | plugin briefing, same `vault-briefing.txt` | Claude Code's own checkpoint hook |
| Posture | `permissions.defaultMode` | `posture` in `nexgen.config.json` | that CLI's own posture file |

An aligned layer is a PASS; a layer one host has and the other lacks is a WARN that
names the gap. The action is almost always `nexgen sync`, the one phase that writes
both sides. The MCP layer compares names only, never an `env` block: a token is
never read, printed or kept.

## What this does not cover

The plugin only sees calls that OpenClaw itself executes: the embedded runner,
which is what the agents on OpenCode Go and on the local model use. An agent whose
runtime is `claude-cli` or `codex` runs its tools inside Claude Code or Codex, where
each CLI's own guardrail, briefing and posture — installed by this same
engine — are already in charge. Two of each on one call would mean two policies to
keep in step; one per call is the point.

`code_execution` runs Python, not a shell line. It is in the watched set so a
`command` field on it would be judged, but its source is not fed to a body that
matches shell patterns, where it would match by accident rather than by meaning.

## What the doctor checks

| Check | Says |
|---|---|
| `guardrail.consulted.openclaw` | the guardrail has been called at least once since it was installed |
| `guardrail.openclaw_plugin` | the plugin is deposited, and whether OpenClaw will load it (`plugins.allow`, `plugins.deny`, an entry switched off) |
| `instructions.openclaw` | every agent workspace's `AGENTS.md` carries the managed block, and which workspace folders do not exist yet |
| `openclaw.parity.*` | one line per layer above: PASS when both hosts hold it, WARN with the action when one does not |
