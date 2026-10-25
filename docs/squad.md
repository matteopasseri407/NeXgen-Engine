# Squad as Data (`squad.yaml`)

NeXgen Engine allows declaring your agent team declaratively in a `squad.yaml` file stored in your private Knowledge Vault.

## Discovery and Precedence

During `nexgen openclaw setup` (or running the installer with `--role server` / `--role standalone`), the engine looks for `squad.yaml` in your vault:
1. `<vault>/squad.yaml`
2. `<vault>/03-INFRA/agent-universal-layer/squad.yaml`
3. `<vault>/03-INFRA/squad.yaml`
4. `~/.config/nexgen/squad.yaml`

- **Without a squad file:** the engine maintains today's standard defaults (`main`, `capo`, `automazioni`, `sviluppo`). Crucially, if you already changed an agent's display name in `openclaw.json`, the setup preserves your custom name and never overwrites it with default strings.
- **With a squad file:** the data in `squad.yaml` wins and is applied systematically to `openclaw.json`.

An example template is available at `03-INFRA/agent-universal-layer/squad.example.yaml`.

## Schema Reference

### `agents`
A map of agent IDs to agent configuration:
- `name`: Display name for the agent in OpenClaw.
- `model`: Model identifier (e.g. `anthropic/claude-opus-5-5`, `opencode-go/deepseek-v4.1-flash`).
- `role`: Description of the agent's function.
- `workspace`: Optional custom path for the agent workspace.
- `thinkingDefault`: Optional reasoning level (e.g. `high`).
- `soul`: Optional personality or guidance prompt. A workspace receives a `SOUL.md` **only** if this field is present in `squad.yaml`. Agents without a soul do not get a default `SOUL.md`.
- `agents_md`: Optional path, relative to the squad file, of the instructions this agent receives as its workspace `AGENTS.md`. See [Instructions per role](#agents_md-and-common_md).
- `default`: Optional `true` mark naming this agent as the installation's default. See [`default`](#default).
- `exec`: Optional execution policy for this agent, written to `agents.entries.<id>.tools.exec`. See [`exec` and the tool policy](#exec-and-the-tool-policy).
- `tools`:
  - `agentToAgent`:
    - `send`: List of target agent IDs this agent is permitted to message. The engine automatically enables `tools.agentToAgent.allow` for all participating agents. An agent this run does not create is left out of both lists: `--no-sviluppo`, a missing `opencode` command or a missing Go key means no `sviluppo` entry, and an id no entry declares is one OpenClaw cannot route.
  - `exec`:
    - `host`: `"node"` or `"gateway"`.
    - `node`: Name of the satellite node machine executing commands for this agent.

### `default`

The agent OpenClaw calls the **system agent**: the one a request uses when no
binding names an agent. The setup writes it to
`agents.defaults.systemAgent.agentId`.

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

The first agent carrying the mark wins when `default:` is absent. A `default:`
that names an agent the squad does not declare writes nothing, and the plan names
the mistake. An owner already configured and not named by the squad is
preserved.

This field matters on a squad with more than one explicit agent: without an
owner, OpenClaw does not hand an unbound message to some arbitrary first agent.
The main session, Talk and the dreaming cron job are refused with "no explicit
owner", which is what a roster with several `agents.entries` and no
`agents.defaults.systemAgent.agentId` produces.

### `exec` and the tool policy

`exec` on an agent is that agent's execution policy, written to
`agents.entries.<id>.tools.exec`:

| Field | Values | Meaning |
| --- | --- | --- |
| `security` | `deny`, `allowlist`, `full` | Whether a command has to match the allowlist. |
| `ask` | `off`, `on-miss`, `always` | When a person is asked to approve a command. |
| `host` | `gateway`, `node` | Where the command runs. |
| `node` | a node name | The satellite machine that runs it. |

An older `tools.exec` block still works: the two are merged, and `exec` wins per
key.

Why this field exists at all. With the `claude-cli` backend, a policy of
`security: "allowlist"` plus `ask: "on-miss"` treats every native Claude tool,
`ToolSearch` included, and every external MCP tool as a miss: OpenClaw asks a
person to approve each call, and denies it when nobody answers. OpenClaw resolves
those native approvals in `resolveCliNativeToolApprovalPlan`, which returns
`allow` only for `security: "full"` with `ask: "off"`. So an agent Claude Code
runs needs that pair, while an agent the gateway itself runs is fine with the
allowlist.

The engine's guardrail plugin checks every tool call (`before_tool_call`) before
it runs, so `full` does not leave the machine unguarded: catastrophic commands
still stop there.

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

Each agent can bring its instructions from the squad, and a section can be shared
by all of them:

```yaml
common_md: "squad/common.md"
agents:
  capo:
    agents_md: "squad/capo.md"
```

Both paths are relative to the squad file, so the two travel together. The setup
writes the role text and then the common section into `AGENTS.md` in the agent's
workspace, the file OpenClaw reads at the start of every session. An agent with
only `common_md` gets that section alone; an agent with neither keeps the manual
the engine ships.

That file starts with the engine's mark:

```html
<!-- nexgen:squad:begin: written from squad.yaml by 'nexgen openclaw setup', and rewritten on the next run -->
```

A file carrying the mark was written by the engine, so the next setup replaces it
and keeps whatever sits above it, which is where `nexgen sync` puts the block
pointing at the canonical instructions. A file without the mark belongs to the
person: it is left byte for byte, and the setup says it skipped it. `SOUL.md`
stays separate and is written only for agents that declare `soul`.

### Node Execution Delegation
When machines connect in the `node` role, they act as headless execution hosts. Node agents are not registered from the satellite machine via RPC; instead, all node agents are declared directly in the central `squad.yaml` file evaluated on the gateway server:
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
Optional dictionary of model entries and custom provider specifications.

### `bindings`
List of channel bindings routing incoming messages on a communication channel to a specific agent:
```yaml
bindings:
  - match:
      channel: "telegram"
    agentId: "main"
```

## Secure Model Authentication

When setup processes squad agents, it automatically inspects each agent's configured model provider. If the corresponding API key is present in your environment (`${VAR}` or environment variables), the engine securely registers it with OpenClaw's credential store:
```bash
printf "%s\n" "$KEY" | openclaw models auth paste-api-key --agent <id> --provider <provider>
```
The key is piped via standard input and is never logged, printed to console, or written in plaintext files.

## Gateway Environment

The setup writes `$OPENCLAW_STATE_DIR/.env` (mode 0600) with the variables the
gateway needs, and never prints their values. Two sources feed it:

- the configuration the setup is writing, including the provider key each agent's
  model needs;
- the MCP servers already mounted in `openclaw.json`, read from the file in
  force: a stdio server cites variables in its arguments and in its `env` block,
  bridge scripts included, and the mounted server table is written by
  `nexgen mcp render` rather than by the setup.

A key already present in the file is never overwritten. Variables that only the
rest of `openclaw.json` cites are not copied in: the mounted server table is the
one source this step owns.

