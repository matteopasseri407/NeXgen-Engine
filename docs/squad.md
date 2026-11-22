# Squad as data (`squad.yaml`)

🇮🇹 [Italiano](it/squad.md)

NeXgen Engine reads your agent team from a `squad.yaml` file in your private Knowledge Vault.

## Discovery and precedence

During `nexgen openclaw setup`, or when you run the installer with `--role server` or `--role standalone`, the engine looks for `squad.yaml` in this order:

1. `<vault>/squad.yaml`
2. `<vault>/03-INFRA/agent-universal-layer/squad.yaml`
3. `<vault>/03-INFRA/squad.yaml`
4. `~/.config/nexgen/squad.yaml`
5. `~/.openclaw/squad.yaml`

Without a squad file, the engine keeps the standard roster (`main`, `capo`, `automazioni`, `sviluppo`). If you already changed an agent's display name in `openclaw.json`, the setup keeps your name and never overwrites it with a default.

With a squad file, the data in `squad.yaml` wins and is applied to `openclaw.json` systematically.

A template is available at `03-INFRA/agent-universal-layer/squad.example.yaml`.

## Schema reference

### `agents`

A map of agent ids to their configuration:

- `name`: display name in OpenClaw.
- `model`: model identifier, for example `anthropic/claude-opus-5-5` or `opencode-go/deepseek-v4.1-flash`.
- `harness`: optional lane selector, one of `claude`, `codex`, `opencode`, `agy`, `none`. Omitted or `none` keeps the provider lane. See [Which lane an agent runs on](#which-lane-an-agent-runs-on).
- `role`: what the agent is for.
- `workspace`: optional custom path for the agent's workspace.
- `thinkingDefault`: optional reasoning level, for example `high`.
- `tool_surface`: optional `lean`, which publishes a minimal tool surface on this agent's model instead of the gateway's whole catalogue. Off when omitted. See [`tool_surface: lean`](#tool_surface-lean).
- `context_tokens`: optional per-call context cap in tokens, written as that model's `contextTokens`. Off when omitted. See [`context_tokens: N`](#context_tokens-n).
- `soul`: optional personality or guidance prompt. A workspace gets a `SOUL.md` only when this field is present. `soul_from` does the same from a file, relative to the squad file, for a longer text somebody can edit. Agents with neither get no default `SOUL.md`.
- `agents_md`: optional path, relative to the squad file, of the instructions this agent receives as its workspace `AGENTS.md`. See [Instructions per role](#agents_md-and-common_md).
- `default`: optional `true` naming this agent as the installation's default. See [`default`](#default).
- `exec`: optional execution policy for this agent, written to `agents.entries.<id>.tools.exec`. See [`exec` and the tool policy](#exec-and-the-tool-policy).
- `reportsTo`: optional agent id this agent answers to. See [Who reports to whom](#reportsto).
- `tools`:
  - `agentToAgent`:
    - `send`: optional list of target agent ids that narrows whom this agent may message. The engine writes no global `tools.agentToAgent.allow`: OpenClaw reads an omitted list as every pair allowed, so an agent without `send` can write to and hear from everyone. An agent this run does not create is left out of a list the squad declares, because `--no-sviluppo`, a missing `opencode` command or a missing Go key means no `sviluppo` entry, and an id no entry declares is one OpenClaw cannot route.
  - `exec`:
    - `host`: `"node"` or `"gateway"`.
    - `node`: name of the satellite machine that executes commands for this agent.
- `subagents`: optional block naming the agents this one may create as children (`allow`) and the model those children run on (`model`). See [Subagents](#subagents).

### `reportsTo`

Hierarchy is data, not a list of names baked into the manuals. Each agent may declare who it answers to:

```yaml
agents:
  main:
    name: "assistente"
  capo:
    name: "capo"
    reportsTo: "main"
  sviluppo:
    name: "sviluppo"
    reportsTo: "capo"
```

Without the field, the roster's own shape decides: `main` reports to nobody, because it faces the person; `capo`, the agent that supervises, reports to `main`; every other agent reports to `capo` when the roster has one, otherwise to `main`, and to nobody when neither exists. The same defaults apply to the engine's own roster when there is no squad file.

The answer lands in each workspace `AGENTS.md` as a delimited block of its own, titled `Organigramma` (or `Org chart`), naming the agent's lead, the agents reporting to it and its peers under the same lead, plus the rule that anyone may write to anyone with `sessions_send`: a task is delegated down to the specialist who owns it, results and blockers go up to the lead, and a peer is asked directly. Both `nexgen openclaw setup` and `nexgen sync` write that block when it is missing or different, so an installation that already exists receives it too, and the shipped manual body only points at it. Nothing outside the block is touched, and a file whose markers are half deleted is reported and left as it is.

A `reportsTo` that names an agent the roster does not declare, or one that closes a loop (`capo` to `automazioni` while `automazioni` reports to `capo`), is reported in the plan and the agent keeps its default lead. Nothing is written that OpenClaw cannot resolve.

### `default`

The agent OpenClaw calls the system agent: the one a request uses when no binding names an agent. The setup writes it to `agents.defaults.systemAgent.agentId`.

```yaml
default: "main"
```

Or per agent, which also works for a squad copied from an older roster:

```yaml
agents:
  main:
    name: "assistente"
    default: true
```

The first agent carrying the mark wins when `default:` is absent. A `default:` that names an agent the squad does not declare writes nothing, and the plan names the mistake. An owner already configured and not named by the squad is preserved.

This field matters on a squad with more than one explicit agent. Without an owner, OpenClaw has no reason to hand an unbound message to the first agent on the list: the main session, Talk and the dreaming cron job are refused with "no explicit owner", which is what a roster with several `agents.entries` and no `agents.defaults.systemAgent.agentId` produces.

### `exec` and the tool policy

`exec` on an agent is that agent's execution policy, written to `agents.entries.<id>.tools.exec`:

| Field | Values | Meaning |
| --- | --- | --- |
| `security` | `deny`, `allowlist`, `full` | Whether a command has to match the allowlist. |
| `ask` | `off`, `on-miss`, `always` | When a person is asked to approve a command. |
| `host` | `gateway`, `node` | Where the command runs. |
| `node` | a node name | The satellite machine that runs it. |

An older `tools.exec` block still works: the two are merged, and `exec` wins per key.

Why the field exists at all. With the `claude-cli` backend, `security: "allowlist"` plus `ask: "on-miss"` treats every native Claude tool, `ToolSearch` included, and every external MCP tool as a miss: OpenClaw asks a person to approve each call and denies it when nobody answers. OpenClaw resolves those native approvals in `resolveCliNativeToolApprovalPlan`, which returns `allow` only for `security: "full"` with `ask: "off"`. An agent Claude Code runs needs that pair; an agent the gateway itself runs is fine with the allowlist.

The engine's guardrail plugin checks every tool call on `before_tool_call` before it runs, so `full` does not leave the machine unguarded: catastrophic commands still stop there.

```yaml
agents:
  sviluppo:
    name: "sviluppo"
    model: "anthropic/claude-opus-5-5"
    exec:
      security: "full"
      ask: "off"
```

### `agents_md` and `common_md`

Each agent can bring its instructions from the squad, and a section can be shared by all of them:

```yaml
common_md: "squad/common.md"
agents:
  capo:
    agents_md: "squad/capo.md"
```

Both paths are relative to the squad file, so the two travel together. The setup writes the role text and then the common section into the agent workspace's `AGENTS.md`, the file OpenClaw reads at the start of every session, and `nexgen sync` writes it again: editing the squad and running the sync is enough, because that is the command a person runs without thinking about it. An agent with only `common_md` gets that section alone; an agent with neither keeps the manual the engine ships.

That file starts with the engine's mark:

```html
<!-- nexgen:squad:begin: written from squad.yaml by 'nexgen openclaw setup', and rewritten on the next run -->
```

A file carrying the mark was written by the engine, so the next run replaces it and keeps whatever sits above it, which is where `nexgen sync` puts the block pointing at the canonical instructions. A file is also the engine's when it is exactly the manual this engine ships for that agent, with or without that block on top: that is a workspace prepared before the squad declared instructions, and treating it as the person's would freeze the old text there forever. Any other file belongs to the person: it is left byte for byte, and the run says it skipped it. One folder holds one `AGENTS.md`, so two agents that share a workspace and declare different manuals are reported instead of guessed. `SOUL.md` stays separate and is written only for agents that declare `soul`.

### Node execution delegation

Machines that connect in the `node` role act as headless execution hosts. Node agents are not registered from the satellite machine over RPC; all of them are declared in the central `squad.yaml` evaluated on the gateway server:

```yaml
agents:
  builder:
    name: "Build Node"
    model: "anthropic/claude-opus-5-5"
    tools:
      exec:
        host: "node"
        node: "desktop-linux"
```

`node: "desktop-linux"` is the same thing on one line: the engine writes `tools.exec` with `host: "node"` and the name, and every command that agent runs runs on that machine. A `tools.exec` block the agent declares itself wins over the shorthand.

That agent also gets a different manual: it is the remote control and the dashboard of the machine, it drives the local harnesses with `nexgen-local sup` (Claude, Codex, agy, OpenCode and the rest, one adapter per harness), it enters a session that is already open, and it says so when the machine is off instead of pretending to have acted. The org chart written into every workspace names the machine that agent controls, which is how the assistant knows whom to hand that PC's work to.

### `models`

Optional dictionary of model entries and custom provider specifications. Each key is a `provider/model` reference and its value is that model's entry in `agents.defaults.models` (see [Model choice per agent](#model-choice-per-agent)). It never lands in the root `models` object of `openclaw.json`: that one is strict and accepts only `mode`, `providers` and `catalogRefresh`.

## Model choice per agent

`model` on an agent is only that agent's default, the model a new session starts on. Every model the machine can actually run appears in the picker of every agent, so the model can be changed per session from the Control UI, the phone app, Telegram or `/model` without editing `squad.yaml`.

`nexgen openclaw setup` writes `agents.defaults.modelPolicy.allow` with one wildcard per reachable provider: `anthropic/*` when Claude Code is logged in, `openai/*` when Codex is, `opencode-go/*` with the flat OpenCode Go key, `ollama/*` and `llama-cpp/*` for the models this machine serves itself, and `google-antigravity-cli/*` when the local `agy` backend plugin is allowed. A wildcard rather than a list of ids, so a model a provider publishes tomorrow is selectable without another setup.

A CLI runtime is a pin on the model, never a provider in that list: a Claude login gives `anthropic/*` plus `agents.defaults.models["anthropic/*"] = {"agentRuntime": {"id": "claude-cli"}}`, the shape OpenClaw's own `doctor --fix` migrates a legacy `claude-cli/*` entry to.

The same setup names the assistant's model as the system primary in `agents.defaults.model`, unless the file already declares one: with the key absent, OpenClaw picks a primary itself, and on a machine with a local model it picks that one.

The squad adds what it names: each `model` on an agent, and each key of its `models` table, becomes an `agents.defaults.models` entry, and its provider a wildcard. That is also why an agent can be switched to a model the squad never mentions.

Two rules protect a choice already made. A policy already in force is extended, never replaced: what the owner wrote stays first and stays there, and a provider the configuration already names stays selectable. No agent's `model` is changed by any of this.

## Which lane an agent runs on

`harness` on an agent decides what actually runs its turn. An agent without the key keeps the lane it has today, so nothing in a setup moves an existing agent anywhere.

| `harness` | The lane | What runs the turn | What it costs |
| --- | --- | --- | --- |
| absent, or `none` | provider | OpenClaw itself, calling the model API it holds a key for (`opencode-go/*`, a local model, ...) | one turn of OpenClaw's own prompt, on the flat plan or the key |
| `opencode`, `claude`, `codex` | ACP session | the real CLI, as its own agent, in that agent's workspace, on its own login | the harness's own context, on the harness's own plan. OpenClaw keeps channels, delivery and policy |
| `agy` | CLI backend | the official `agy` CLI, through the `google-antigravity-cli` plugin | OpenClaw's prompt plus the loopback tool surface |

The ACP lane is the one that changes the bill: a turn carries the harness's own prompt and skills instead of OpenClaw's, which is the difference between a few thousand tokens of context and the 78,717 tokens measured for one `claude-cli` turn on this engine (`docs/proposals/daru-burn.md`).

Two things are fixed, not configurable:

- `agy` never becomes an ACP session. It is a CLI backend on Google's own client; ACP for Antigravity would need Google's separate proprietary server, which carries a Google plan outside Google's clients. The plugin is requested by a model that names `google-antigravity-cli/...`, which is a separate decision from this key.
- An ACP lane is written as **both halves, or not at all**: `agents.entries.<id>.runtime` and a `bindings` entry of `type: "acp"`. A runtime block with no bound conversation answers a direct chat with the default model instead of the harness.

An ACP agent needs a channel binding that routes a conversation to it. The setup then writes one runtime block and one binding for that conversation:

```yaml
agents:
  sviluppo:
    harness: "opencode"
bindings:
  - match:
      channel: "telegram"
    agentId: "sviluppo"
```

```json5
{
  agents: { entries: { sviluppo: { runtime: { type: "acp", acp: {
    agent: "opencode", backend: "acpx", mode: "persistent", cwd: "<that agent's workspace>",
  } } } } },
  bindings: [{ type: "acp", agentId: "sviluppo", match: { channel: "telegram" },
    acp: { mode: "persistent", cwd: "<that agent's workspace>", label: "sviluppo-opencode" } }],
}
```

`nexgen openclaw plan` names the cases it cannot write, and leaves that agent on the lane it already has: a value that is not one of the five above, a harness that is not installed on this machine, an agent that no binding routes a conversation to, an agent with no workspace.

On an ACP lane the agent's `model` is handed to the harness as its model selection, so it has to name a model that harness can run, not one OpenClaw holds a key for. Model control over ACP is adapter-dependent: if the adapter cannot accept a selection, the harness keeps its own default and the entry's `model` stays advisory.

The setup never removes those blocks. The merge keeps every key that may be yours, so removing `harness:` from the squad does not move the agent back: to return it to the provider lane, remove the entry's `runtime` block and its `type: "acp"` binding in `openclaw.json`.

### `bindings`

A list of channel bindings that route incoming messages on a communication channel to a specific agent:

```yaml
bindings:
  - match:
      channel: "telegram"
    agentId: "main"
```

A binding also brings that channel's bundled plugin into `plugins.allow` (`telegram` for the example above), the entry OpenClaw adds itself the first time a channel is configured: a channel whose plugin is not allowed routes nothing. A channel no bundled plugin serves is left to the plugin the person installed and allowed.

## Opt-in consumption levers

Three settings bound what a turn is allowed to spend. All three are off until you write them: an agent that declares none of them keeps the surface, the context and the behaviour it has today. The numbers below come from `docs/proposals/daru-burn.md`, where each one says whether it was measured or estimated, and the baseline is the `capo` turn measured on this engine: 74 model calls, about 78,000 tokens of prompt in each call, about 5.77 M tokens per turn.

| Key | Where | What it writes | Tokens per turn, before | Tokens per turn, after | Recommended |
| --- | --- | --- | --- | --- | --- |
| `tool_surface: lean` | on an agent | `tools.byProvider["<that agent's model>"] = { profile: "minimal", alsoAllow: [...] }` | 78,000 per call, 5.77 M per turn | ~63,000 per call, ~4.66 M per turn (-19%) | yes, for an agent on a CLI backend |
| `context_tokens: N` | on an agent | `models.providers.<provider>.models[].contextTokens` | no per-call ceiling: the worst case is the model's window, so 74 M per turn at 1 M | same per-turn median, but the worst case is 74 x N: 14.8 M per turn at 200,000 | `200000`, then measure |
| `acp_admission: true` | at the root | `governor.acp.agents` in the plugin's sidecar | a turn started on a spent plan runs until it fails mid-loop, paying for the calls it made | 0 model calls, 0 tokens: one line naming the plan and the window, before the harness starts | yes, once a `harness:` lane exists |

Calls per turn: none of the three changes them. `tool_surface` and `context_tokens` make each call cheaper and bound the total; `acp_admission` removes the calls of a turn that would have died anyway. The call count itself is the one lever the pinned OpenClaw does not expose, and `docs/proposals/daru-burn.md` describes it rather than pretending otherwise.

### `tool_surface: lean`

```yaml
agents:
  capo:
    model: "anthropic/claude-opus-5-5"
    tool_surface: "lean"
```

On a CLI-backend lane the gateway's own tool catalogue reaches the harness eagerly, so 51 schemas ride in every model call: they were 112,239 of the ~264,000 prompt characters of the measured turn, about 33,000 of its 78,000 tokens. `profile: minimal` publishes OpenClaw's own small allowlist, and `alsoAllow` adds back what the agent actually uses: shell, files, web, the session tools, memory, the supervisor tools for the agent that supervises, and the four `lazy-mcp` meta-tools.

Connectors stay reachable: the loopback deny already removes their schemas, and the `lazy-mcp` gateway is the one road left open, so Vault, GitHub, Vercel and the rest are called through `lazy-mcp__lazy_call` as before. Nothing on the agent's capability list is dropped, and the engine writes no `allow` list (OpenClaw refuses `allow` beside `alsoAllow` in one scope; this entry uses `alsoAllow` only).

The key is the model, not the agent, because that is what `tools.byProvider` means: every agent running that model gets the lean surface. Give the agent its own model if that is not what you want.

### `context_tokens: N`

```yaml
agents:
  capo:
    model: "anthropic/claude-opus-5-5"
    context_tokens: 200000
```

The cap is the active input of one model call. On the `claude-cli` road it becomes Claude Code's own auto-compact window, so a conversation is summarised at the cap instead of growing towards a 1,000,000-token worst case: 74 calls against a 200,000 cap is 14.8 M tokens, against 74 M at the native window. The trade is real and it is yours: older turns become a summary sooner.

Only `contextTokens` is written, never `contextWindow`: the cap is a decision, the native window is the model's. When two agents name the same model with different numbers, the smallest wins.

**Experimental, and why.** This is the one lever that writes a `models` array for a provider a bundled plugin owns (here `anthropic`). The pinned release documents the merge: an entry you author in `models.providers.<id>.models` keeps its own rows, and the plugin's catalogue stops contributing only when a provider-level `baseURL` points outside the plugin's declared endpoints, which this engine never writes (`openclaw models list` documentation of the pinned release). So the auth route should survive. Should is not the same as proved on your machine: the engine has not watched a live turn on your lane, so verify once. Run `openclaw config validate`, then one real turn, then `openclaw models list --provider anthropic` and check the entry still carries its auth. If the model list changes shape, remove the key: the agent keeps working and stops compacting early.

### `acp_admission: true`

```yaml
acp_admission: true
agents:
  sviluppo:
    harness: "opencode"
    model: "opencode-go/deepseek-v4.1-flash"
```

An ACP session is the harness running as itself, so OpenClaw's fallback chains are never consulted for it: without this branch the lane with the most context per turn is the one with no gate at all. With it, the plugin reads `nexgen-quota-state.json` (the readings the hourly beat already writes) before a turn, and when the plan the agent's model names is at or above the `block_at` line of your policy, it answers with one line naming the plan and the window instead of starting a harness turn.

It costs no model call: the answer is the plugin's own text. Nothing happens when the state is missing, older than the policy allows, has no reading for that plan, or reads below the line; and the branch is written only when `acp_admission: true` is in the squad, so a machine that never asked for it never refuses a turn.

## Subagents

The head of the squad can create a specialist as a child run, and read its result, instead of sending it a message and keeping the whole task in its own conversation. Two blocks decide who may do it, on which model, and how far fan-out may go; both are written to the keys the pinned release documents (`docs/tools/subagents/nesting.md`, `docs/gateway/config-agents/entries-and-multi-agent.md`).

```yaml
agents:
  main:
    subagents:
      allow: ["posta", "capo", "automazioni"]
      model: "opencode-go/deepseek-v4.1-flash"

subagents:
  maxSpawnDepth: 2
  maxChildrenPerAgent: 6
  maxConcurrent: 4
  runTimeoutSeconds: 900
```

```json5
{
  agents: {
    entries: { main: { subagents: {
      allowAgents: ["posta", "capo", "automazioni"],
      model: "opencode-go/deepseek-v4.1-flash",
    } } },
    defaults: { subagents: {
      maxSpawnDepth: 2, maxChildrenPerAgent: 6,
      maxConcurrent: 4, runTimeoutSeconds: 900,
    } },
  },
}
```

`allow` is the list of agents this one may target with `sessions_spawn.agentId`, and it becomes `allowAgents`. Without the key OpenClaw allows the same agent only, so an agent that declares nothing keeps delegating by message, as it does today. Every id must be an agent the squad declares: a target OpenClaw cannot route would be a spawn that fails later, silently, and a wildcard `["*"]` is refused on purpose because the point of this list is that it follows the roster. An empty list (`allow: []`) is written as declared and forbids cross-agent spawns for that agent; naming the agent itself (`allow: ["main"]`) is allowed, since OpenClaw wants the requester's id for a self-targeted spawn. A typo in the block is an error that names the key, never a key the gateway ignores.

`model` is the model a child of this agent runs on, for a spawn that stays on the same agent. A cross-agent spawn runs on the target agent's own `model`, so an agent's `subagents.model` does not override the specialist's: `docs/tools/subagents/tool-reference.md` in the pinned release says so. Omitted, the fallback is `agents.defaults.subagents.model`, which the `runtime: subagent_model` key already writes.

The root block is the squad's ceiling on all spawning, and the four values are written even when the file says nothing, because OpenClaw's own defaults are looser: depth 5, five children per session, eight concurrent runs and no timeout at all. Depth 2 lets the head's children create leaves and nothing deeper; six children bound one fan-out; four concurrent runs bound what one orchestrator starts at once; 900 seconds bound a child that never answers. The four are the squad's: a value already in `openclaw.json` loses to the declared one and to the default. The first two have a documented range (`maxSpawnDepth` 1-5, `maxChildrenPerAgent` 1-20) and the setup refuses a value outside it instead of writing a key the gateway would clamp or ignore. `runTimeoutSeconds: 0` is refused with the reason: in OpenClaw it means no timeout, so a child that never answers runs until somebody stops it.

At the depth ceiling a child is a leaf: OpenClaw takes the recursive orchestration tools (`sessions_spawn`, `subagents`) away from it, so depth 2 is what keeps a fan-out from becoming a tree.

## Secure model authentication

While processing squad agents, the setup inspects each agent's configured model provider. If the matching API key is in your environment (`${VAR}` or an environment variable), the engine registers it with OpenClaw's credential store:

```bash
printf "%s\n" "$KEY" | openclaw models auth paste-api-key --agent <id> --provider <provider>
```

The key travels over standard input and is never logged, printed to the console or written in plaintext to a file. An agent whose model runs on a local CLI backend (`claude-cli`, `codex`, `agy`) gets no key registered: that login stays in the CLI that already has it, and the setup says so once per agent.

## Gateway environment

The setup regenerates `$OPENCLAW_STATE_DIR/.env` (mode 0600) from the values in force and never prints them. Two sources feed it:

- the configuration being written, including the provider key each agent's model needs;
- the MCP servers already mounted in `openclaw.json`, read from the file in force: a stdio server cites variables in its arguments and in its `env` block, bridge scripts included. The mounted server table is written by `nexgen mcp render`, not by the setup.

The file is regenerated every run, so a token rotated in the engine's secret store does not stay alive here at its old value. Keys the engine does not reference are yours and stay untouched, with one exception: a key no `claude-cli` agent reads is taken out, because it breaks the model catalogue.
