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
| Guardrail | `<state dir>/extensions/nexgen-guardrail/` | the guard's permissions phase |

## MCP connectors

The manifest's servers are written into the `mcp.servers` table of `openclaw.json`
and nothing else in that file is touched: models, channels, gateway settings and
servers the manifest does not declare are OpenClaw's own. A machine where OpenClaw
was never set up has no config file, and the renderer does not invent one.

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

## Guardrail

OpenClaw's hook is a JavaScript callback inside the gateway process, so the engine
ships a native plugin and deposits it, byte for byte, in
`<state dir>/extensions/nexgen-guardrail/`:

```
extensions/nexgen-guardrail/
  index.mjs                     the adapter
  package.json                  openclaw.extensions -> ./index.mjs
  openclaw.plugin.json          id: nexgen-guardrail, onStartup
  nexgen-guardrail-core.mjs     the shared core every adapter imports
  nexgen-guardrail.config.json  which body, with what timeout, the audit file
  hooks/<body>.mjs              the guardrail body (the private policy)
```

On `before_tool_call`, a tool call that carries a shell command (`exec`,
`process`, `terminal`, `code_execution`) is handed to the guardrail body in the
same stdin/stdout JSON that Claude Code's own `PreToolUse` hooks use. A denial
blocks the call with the body's reason; a question becomes an approval request for
the person; an allow leaves the call alone. A failure never becomes an allow: a
missing core, an unreadable sidecar or a body that crashes blocks, or asks, and
OpenClaw itself blocks the call when the handler throws.

Two things about enabling it:

- **The plugin is deposited, not enabled.** Editing `plugins.*` while the gateway
  runs breaks the gateway, so the engine never does it in the guard cycle.
  `nexgen openclaw setup` enables it with the gateway stopped. Until then
  `nexgen doctor` reports it as deposited and not enabled, which is the truth and
  not a failure of the sync.
- **There is no posture for OpenClaw.** Command policy and exec approvals are
  OpenClaw's own schema (`tools.exec`), and the engine has no verified rendering
  for it. A posture the private permission manifest asks for is reported as
  "nothing applied" rather than guessed at, and `openclaw.json` is never rewritten
  by the permissions phase.

## What this does not cover

The guardrail plugin only sees calls that OpenClaw itself executes: the embedded
runner, which is what the agents on OpenCode Go and on the local model use. An
agent whose runtime is `claude-cli` or `codex` runs its tools inside Claude Code or
Codex, where each CLI's own guardrail — installed by this same engine — is already
in charge. Two guardrails on one call would mean two policies to keep in step; one
per call is the point.

`code_execution` runs Python, not a shell line. It is in the watched set so a
`command` field on it would be judged, but its source is not fed to a body that
matches shell patterns, where it would match by accident rather than by meaning.

## What the doctor checks

| Check | Says |
|---|---|
| `guardrail.consulted.openclaw` | the guardrail has been called at least once since it was installed |
| `guardrail.openclaw_plugin` | the plugin is deposited, and whether OpenClaw will load it (`plugins.allow`, `plugins.deny`, an entry switched off) |
| `instructions.openclaw` | every agent workspace's `AGENTS.md` carries the managed block, and which workspace folders do not exist yet |
