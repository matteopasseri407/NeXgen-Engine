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
- `tools`:
  - `agentToAgent`:
    - `send`: List of target agent IDs this agent is permitted to message. The engine automatically enables `tools.agentToAgent.allow` for all participating agents.
  - `exec`:
    - `host`: `"node"` or `"gateway"`.
    - `node`: Name of the satellite node machine executing commands for this agent.

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
