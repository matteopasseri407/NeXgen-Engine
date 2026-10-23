# OpenClaw, the harness with the interface

OpenClaw (MIT, npm `openclaw`) is NeXgen Engine's official harness.
It is the entry point with an interface, a web panel and a phone app paired by QR, from which a person uses
the flat subscriptions they already pay for and the models on their own machine.
It is also the fifth CLI the sync writes, but that is the smaller half of its role.

The engine does not fork it and does not own its file.
It generates only the sections OpenClaw needs to reach the subscriptions and the Vault, and leaves everything
else to OpenClaw and to the person using it.

## What the engine detects

`nexgen openclaw plan` and `nexgen openclaw doctor` read this, and nothing else:

| What | How |
| --- | --- |
| OpenClaw | the global npm package's `package.json`, located with `npm root -g`; the version must match `agent-universal-layer/openclaw/pin.json` (2026.10.6) |
| The configuration | `$OPENCLAW_CONFIG_PATH`, else `$OPENCLAW_STATE_DIR/openclaw.json`, else `~/.openclaw/openclaw.json` |
| Claude | the `claude` binary on PATH and `~/.claude/.credentials.json` present. On macOS the login lives in the Keychain: the engine reports "not verifiable" and trusts the binary |
| Codex | the `codex` binary on PATH and `auth.json` under `$CODEX_HOME`, or `~/.codex` |
| OpenCode Go | only whether `OPENCODE_API_KEY` is set. The value is never read |
| Ollama | `GET $OLLAMA_HOST/api/tags`, default `http://127.0.0.1:11434`, short timeout |
| llama.cpp | `GET $NEXGEN_LLAMACPP_URL/v1/models`, default `http://127.0.0.1:8080` |
| n8n | the module declared `local` in the engine, with its environment gates set |

No credential file is opened, ever: the engine asks only whether the file exists.
The two local backends are probed through an injectable client, so a machine without them costs one short
timeout and no more.

The engine never downloads a model.
If it finds none, the plan prints what to start and stops there.

## The three agents

| Agent | What it does | Model, in the order tried |
| --- | --- | --- |
| `main` (assistente) | talks to the person, finds things in the Vault, hands work on | `opencode-go/deepseek-v4.1-flash`, then a local model, then Codex, then Claude |
| `capo` (code) | writes and verifies code | `anthropic/claude-opus-5-5` with `thinkingDefault: "high"` and the embedded runtime, then Codex `openai/gpt-6-luna`, then `opencode-go/deepseek-v4-pro` |
| `automazioni` | builds and repairs workflows | only when n8n is active: `opencode-go/deepseek-v4-pro`, then Claude |

An agent with no model available is not created, and the plan says which one and why.
The fallbacks are ordered so the flat subscription is used before a paid key, and so a local model is used
before a cloud one only for the assistant, where latency matters more than depth.

Each agent gets an `AGENTS.md` in the engine's language: who it is, what it does and does not do, when to
hand work to another agent with `sessions_send`, that the Council is called with `nexgen council`, and that
the Vault is read and written with the `vault-library__*` tools.
The code agent also gets `CLAUDE.md` and `.claude/settings.json`, so Claude Code behaves the same way when it
is reached directly.

Agents may write to each other only along the pairs that exist: `main` with `capo`, and `main` with
`automazioni`. Without a second agent the block is not written at all, because an empty allow list means
something different from "nobody to talk to".

## The cage around a local model

A local model in OpenClaw can reach the same tools as any other, and a 12B model with a shell is a bad idea.
Every local model therefore gets an entry in `tools.byProvider["<provider>/<id>"]`:

- `profile: "messaging"`;
- `alsoAllow`: `read`, `view_image`, `pdf`, `tts`, `memory_search`, `memory_get`;
- `deny`: the `group:web`, `group:runtime`, `group:ui`, `group:nodes` and `group:automation` families,
  `write`, `edit`, `apply_patch`, `playwright__*`, `lazy-mcp__lazy_mutate`, and the writing tools of every MCP
  server that has them.

Writing tools are named `server__tool` in OpenClaw, so the deny list is derived from one table in
`profile.py` and not scattered through the code. Today the table covers `n8n-mcp` (create, delete, update,
publish, execute, test and the rest), `vault-library` (create, append, update, section) and `drive` (the two
upload steps).

Ollama ids are written exactly as `/api/tags` returns them but without the `:latest` suffix.
With the suffix OpenClaw does not recognise the model and guesses the context window wrong.

## Terms of service

OpenClaw uses the person's own subscriptions through the logins that already exist on the machine.
The engine writes no API key of its own there, and no credential value ever enters the configuration it
produces.

No Google plan is configured in OpenClaw: Gemini and Antigravity are allowed only inside Google's own
clients. The engine never writes a `google` plugin, and `nexgen openclaw doctor` fails a configuration that
has one, including an OAuth entry under another name. A `gemini` seat in `acp.allowedAgents` is a warning,
because it is allowed by the protocol and not by the terms.

## Windows, the traps that cost an afternoon

- `openclaw gateway restart` does not restart on Windows. Stop the processes and relaunch the service, or
  `nexgen openclaw restart` once B2 ships it.
- Plugins are not changed hot. After a change to `plugins`, restart, and expect the first start to be slow.
- A cold start takes about five minutes. The panel looks dead while it waits; it is not.
- The Companion app from the Microsoft Store installs a second gateway. Two gateways fight over the same port
  and the same state, and `nexgen openclaw doctor` names the Store package when it finds it.

## Commands

`nexgen openclaw plan [--json] [--lan] [--acpx]` reads the configuration in force, computes what the engine
would write, and prints what it would add, what it would change, what it no longer proposes, and which keys
are the person's and stay. It writes nothing.

`nexgen openclaw doctor [--json]` checks installation, version against the pin, the configuration's validity,
`:latest` ids, caged local models, the heartbeat, the gateway's bind and authentication, Google entries, ACP
seats, agents on subscriptions this machine does not have, whether the gateway is listening, and a second
gateway from the Microsoft Store. `nexgen doctor` runs the same checks as part of its own report, and says
nothing at all on a machine that never installed OpenClaw and never declared the module.

`nexgen openclaw setup` and `nexgen openclaw restart` arrive with unit B2. They reuse the same detection and
the same pure function that `plan` prints, so what the plan promises is what the setup writes.
