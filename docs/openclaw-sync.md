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
| Briefing, posture, engine | `<state dir>/extensions/nexgen/nexgen.config.json` | the permissions phase and the module state |

## MCP connectors

The manifest's servers are written into the `mcp.servers` table of `openclaw.json`
and nothing else in that file is touched: models, channels, gateway settings and
servers the manifest does not declare are OpenClaw's own. A machine where OpenClaw
was never set up has no config file, and the renderer does not invent one.

OpenClaw can also act as an MCP server for the other harnesses (`claude`, `codex`,
`antigravity`, `opencode`) via `openclaw mcp serve`. The canonical manifest entry is
`enabled: false` by default and flipped to `true` by `nexgen openclaw setup`. The
OpenClaw renderer explicitly excludes this server when rendering `openclaw.json`, so
OpenClaw never mounts itself recursively.

Conversely, ClawHub plugins installed in OpenClaw that wrap external or bundled MCP
servers can be adopted into the canonical library using `nexgen openclaw adopt --mcp`.
Once adopted, their underlying servers enter `manifest.yaml` and are materialized for
all target harnesses across all machines.

In `mcp.servers.*.env`, every `${VAR}` stays a reference in `openclaw.json`:
OpenClaw substitutes it itself, and a credential must not be copied out of the
environment into a file. The exception is a value whose references are all
engine-owned paths or ports (`AGENT_VAULT_DATA`, `AGENT_ENGINE_ROOT`,
`KNOWLEDGE_VAULT_PATH`, the tunnel ports), which is written resolved because the
gateway's environment is not the engine's. A value that mixes the two stays a
reference: a missing port is a better outcome than a leaked secret.

### When the gateway is in WSL

The app-owned WSL distro cannot launch the Windows commands the manifest declares for
stdio connectors. `nexgen mcp bridge start` serves those same connectors on the Windows
side as MCP Streamable HTTP, and the renderer writes, for a connector the engine asks it
to bridge, `url`, `transport: "streamable-http"` and
`Authorization: Bearer ${NEXGEN_MCP_BRIDGE_TOKEN}` instead of `command` and `args`. Nothing
else about that connector changes: its timeouts and its tool filter travel with it.

`nexgen_core.mcp_bridge.bridged_http_servers()` builds that map from the running bridge and
`McpRenderer.openclaw_http_servers` is where the engine hands it over; with no bridge
running the map is empty and every connector renders as before, because a connector
pointed at a dead port fails at the first call. The token stays a reference in the file:
it is generated into the engine's deposit on this machine, and the gateway must find
`NEXGEN_MCP_BRIDGE_TOKEN` in its own environment inside the distro for the substitution to
resolve.

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

## The plugin: one plugin, one file per job

OpenClaw's hooks are JavaScript callbacks inside the gateway process, so the engine
ships one native plugin and deposits it, byte for byte, in
`<state dir>/extensions/nexgen/`:

```
extensions/nexgen/
  index.mjs                     registers the jobs; the guardrail comes first
  guardrail.mjs                 the gate on before_tool_call
  briefing.mjs                  the vault briefing after a compaction
  quota.mjs                     `/quota`: the lines the engine already rendered
  workshop.mjs                  a log line when the Skill Workshop learned a skill
  governor.mjs                  the fallback handover on before_model_resolve
  council.mjs                   /council: the AI Council, started now and reported when it ends
  sup.mjs                       /sup: the supervised tasks, their units and their spend
  sup-tools.mjs                 the supervision an agent may drive, as tools
  evidence.mjs                  a local model's answer, held to its own tool receipts
  engine-run.mjs                how any of them starts the engine: no shell, a deadline
  engine-config.mjs             reads nexgen.config.json, once for every job that has a switch
  session.mjs                   which session a hook event belongs to
  package.json                  openclaw.extensions -> ./index.mjs
  openclaw.plugin.json          id: nexgen, onStartup, the tool names it owns
  nexgen-guardrail-core.mjs     the shared core every adapter imports
  nexgen-guardrail.config.json  which guardrail body, with what timeout, the audit file
  nexgen.config.json            the engine's switches: briefing, posture, engine, governor, evidence
  nexgen-quota-state.json       what the beat last read, for the governor
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

### The `/quota` command

`quota.mjs` registers a chat command and answers with the lines the engine has
already rendered: `nexgen quota --json` prints `lines`, and the plugin forwards
that text without formatting a single number of its own, so the phone and the
terminal cannot disagree about a percentage. What each provider gives, and what
cannot be measured from here, is `docs/quota.md`.

The engine's path is deposited, not looked up: `nexgen.config.json` carries
`engine.command` and `engine.entry`, the interpreter and the CLI module, because
the `nexgen` launcher on Windows is a `.cmd` and needs `cmd.exe` -- the plugin
spawns with `shell: false` and never opens a shell. The call has a time limit,
discards stderr, and turns every failure into one sentence in the chat: no
engine path, no start, too slow, or an error exit. A chat that loses `/quota` is
a worse chat, not a broken gateway. On an OpenClaw whose API has no
`registerCommand` the job registers nothing and breaks nothing.

### The Workshop notice

OpenClaw is where the owner creates: the Skill Workshop learns a skill into one
agent's own state folder, which no other CLI reads. `workshop.mjs` listens to the
`skill_changed` hook and, for a create or an update whose `source` is `workshop`,
writes one line in the gateway log: a new skill is in the Workshop, and
`nexgen openclaw adopt` makes it canonical. Observation only -- it returns nothing,
cannot block the change, writes no file and runs no command, because adoption
rewrites the library and is the person's decision. A removal leaves nothing to
adopt, and an event from ClawHub or a source install is that installer's business,
so both are silent.

### Evidence

The check the old local lane had, moved into the harness that now runs the loop, so
it covers every agent on a local model instead of one action at a time. On
`before_agent_finalize` the plugin sees the finished answer (`lastAssistantMessage`),
the provider, and the turn's `messages`; on `after_tool_call` it collects the receipts
as they land. `verifyAnswer` is a pure function of the text and those receipts, so the
verdict is testable without a gateway, a model or a network.

What the verdict can say, and what it can never do:

| Claim in the answer | Receipt it needs |
|---|---|
| a read (`ho letto`, `I read`) | a successful read-tool receipt from this turn |
| a search (`ho cercato`, `I searched`) | a successful search-tool receipt from this turn |
| a command or a run (`ho eseguito`, `I ran`) | a successful shell/exec receipt from this turn |
| tests or verification (`ho verificato`, `tests passed`) | a successful shell/exec receipt from this turn |
| a write (`ho salvato`, `file aggiornato`, `I saved`) | a successful write-tool receipt from this turn |
| a cited source (`[01-NOTE/x.md]`) | that path among the files this turn read |

The tables carry English and Italian together, and no configured language decides which
half is read: the engine is public and answers in English by default, so an unsupported
English answer is found exactly like an Italian one, and an answer that mixes the two is
judged claim by claim. The note is English, like every other sentence the plugin renders
itself: the engine translates its own words, and the plugin has no way to know the
language of the person reading.

An unsupported answer is not dropped and not passed in silence:

1. the model gets one more pass (`revise`, with `retry.maxAttempts: 1` and a key of
   the engine's own) and is told which claims have no receipt;
2. if it comes back unsupported anyway, or a stop hook is already asking for a pass,
   the answer is delivered with a visible note, `Unverified claims: ...`,
   under it.

The switch and the providers are not in the plugin. They are the `evidence` section
of `nexgen.config.json`, written by the deposit from the configuration in force:
`{"enabled": true, "providers": ["llama-cpp", "ollama"]}`. A machine with no local
model gets `{"enabled": false, "providers": []}`, and a cloud model is never in the
list, so it is never checked.

Two limits worth knowing. `revise` needs a finalization integration: the embedded
runner and the Codex hook relay have one, the Copilot harness does not, and a run that
already had a deterministic side effect is not rewound, which is why the note is the
path that does not depend on the harness. The note is added to the delivery that
carries the judged text, recognized by its first line: a channel that rewrites the
answer beyond recognition gets no note, and no other message of that session is ever
annotated in its place. The phrase tables are long and bilingual, not exhaustive: a
claim worded in a way neither list carries is still checked where the rule does not
depend on a phrase (a cited source, an invented result, facts with nothing retrieved).

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
| `openclaw.evidence` | whether the evidence check is on, and for which local models; a WARN when the plugin is enabled but the switch or the conversation access is missing |
| `openclaw.parity.*` | one line per layer above: PASS when both hosts hold it, WARN with the action when one does not |
| `openclaw.unadopted` | how many skills and servers OpenClaw created and the canonical library does not declare yet, with `nexgen openclaw adopt` as the action; silent when either manifest cannot be read |
