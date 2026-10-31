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
- `role`: what the agent is for.
- `workspace`: optional custom path for the agent's workspace.
- `thinkingDefault`: optional reasoning level, for example `high`.
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

### `models`

Optional dictionary of model entries and custom provider specifications. Each key is a `provider/model` reference and its value is that model's entry in `agents.defaults.models` (see [Model choice per agent](#model-choice-per-agent)). It never lands in the root `models` object of `openclaw.json`: that one is strict and accepts only `mode`, `providers` and `catalogRefresh`.

## Model choice per agent

`model` on an agent is only that agent's default, the model a new session starts on. Every model the machine can actually run appears in the picker of every agent, so the model can be changed per session from the Control UI, the phone app, Telegram or `/model` without editing `squad.yaml`.

`nexgen openclaw setup` writes `agents.defaults.modelPolicy.allow` with one wildcard per reachable provider: `anthropic/*` when Claude Code is logged in, `openai/*` when Codex is, `opencode-go/*` with the flat OpenCode Go key, `ollama/*` and `llama-cpp/*` for the models this machine serves itself, and `google-antigravity-cli/*` when the local `agy` backend plugin is allowed. A wildcard rather than a list of ids, so a model a provider publishes tomorrow is selectable without another setup.

A CLI runtime is a pin on the model, never a provider in that list: a Claude login gives `anthropic/*` plus `agents.defaults.models["anthropic/*"] = {"agentRuntime": {"id": "claude-cli"}}`, the shape OpenClaw's own `doctor --fix` migrates a legacy `claude-cli/*` entry to.

The same setup names the assistant's model as the system primary in `agents.defaults.model`, unless the file already declares one: with the key absent, OpenClaw picks a primary itself, and on a machine with a local model it picks that one.

The squad adds what it names: each `model` on an agent, and each key of its `models` table, becomes an `agents.defaults.models` entry, and its provider a wildcard. That is also why an agent can be switched to a model the squad never mentions.

Two rules protect a choice already made. A policy already in force is extended, never replaced: what the owner wrote stays first and stays there, and a provider the configuration already names stays selectable. No agent's `model` is changed by any of this.

### `bindings`

A list of channel bindings that route incoming messages on a communication channel to a specific agent:

```yaml
bindings:
  - match:
      channel: "telegram"
    agentId: "main"
```

A binding also brings that channel's bundled plugin into `plugins.allow` (`telegram` for the example above), the entry OpenClaw adds itself the first time a channel is configured: a channel whose plugin is not allowed routes nothing. A channel no bundled plugin serves is left to the plugin the person installed and allowed.

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
