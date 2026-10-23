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
| OpenCode Go | a key is available if `OPENCODE_API_KEY` is set, or OpenClaw already holds an `opencode-go` auth profile, or OpenCode itself is logged in for it. OpenCode's `auth.json` is read for its top-level key names only, never for a value |
| Ollama | `GET $OLLAMA_HOST/api/tags`, default `http://127.0.0.1:11434`, then `POST /api/show` per model for its context window and capabilities, short timeouts |
| llama.cpp | `GET $NEXGEN_LLAMACPP_URL/v1/models`, default `http://127.0.0.1:8080`, and `/props` for the context window |
| n8n | the module declared `local` in the engine, with its environment gates set |

No credential file is opened to read a value, ever: the engine asks whether the file exists, with one
deliberate exception. OpenCode's `auth.json` is parsed to see which providers have a top-level key, so an
existing `opencode-go` login can be reused; no value is copied anywhere.
The two local backends are probed through an injectable client, so a machine without them costs one short
timeout and no more.

The engine never downloads a model.
If it finds none, the plan prints what to start and stops there.

## The agents

| Agent | What it does | Model, in the order tried |
| --- | --- | --- |
| `main` (assistente) | talks to the person, finds things in the Vault, hands work on | `opencode-go/deepseek-v4.1-flash`, then a local model, then Codex, then Claude |
| `capo` (code) | writes and verifies code | `anthropic/claude-opus-5-5` with `thinkingDefault: "high"` and the embedded runtime, then Codex `openai/gpt-6-luna`, then `opencode-go/deepseek-v4-pro` |
| `automazioni` | builds and repairs workflows | only when n8n is active: `opencode-go/deepseek-v4-pro`, then Claude |
| `sviluppo` (developer) | works on the project, one to one, inside OpenCode | `opencode-go/deepseek-v4.1-flash`, only when the `opencode` command and the OpenCode Go key are both there |

An agent with no model available is not created, and the plan says which one and why.
The fallbacks are ordered so the flat subscription is used before a paid key, and so a local model is used
before a cloud one only for the assistant, where latency matters more than depth.

Each agent gets an `AGENTS.md` in the engine's language: who it is, what it does and does not do, when to
hand work to another agent with `sessions_send`, that the Council is called with `nexgen council`, and that
the Vault is read and written with the `vault-library__*` tools.
The code agent also gets `CLAUDE.md` and `.claude/settings.json`, so Claude Code behaves the same way when it
is reached directly.

Agents may write to each other only along the pairs that exist: `main` with `capo`, `main` with
`automazioni`, and `sviluppo` with both. Without a second agent the block is not written at all, because an
empty allow list means something different from "nobody to talk to".

## The OpenCode agent, one to one

`sviluppo` is not another OpenClaw agent answering in its own place: it is OpenCode itself, launched over
ACP, so the same CLI, the same logins, the same skills and the same guardrail apply whether a person opens
OpenCode in a terminal or writes to the agent from the phone.

Three settings make that real, and all three are needed:

- `runtime.type: "acp"` with `runtime.acp.agent: "opencode"`, `backend: "acpx"` and `mode: "persistent"`;
- `model.primary` set to `opencode-go/deepseek-v4.1-flash`: for an ACP agent that field chooses the
  harness's model, and without it OpenClaw runs whatever the harness defaults to, which is how an agent
  once answered with the wrong model;
- `acp.allowedAgents` listing only the harnesses this machine can actually run, chosen from `claude`,
  `codex` and `opencode`. `gemini` is never written: Google's terms do not allow its client outside
  Google's own applications.

The `acpx` plugin goes in `plugins.allow` with `permissionMode: "approve-all"` and
`nonInteractivePermissions: "deny"`: a non-interactive session has no way to click a permission prompt, so
it either proceeds or denies and continues, never hangs. `--no-sviluppo` leaves the agent out even when
both prerequisites are there.

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
publish, execute, test and the rest), `vault-library` (create, append, update, section), `drive` (the two
upload steps) and `firecrawl` (scrape, map, extract, crawl, agent, browser, interact; search and reads stay
allowed).

Ollama ids are written exactly as `/api/tags` returns them but without the `:latest` suffix.
With the suffix OpenClaw does not recognise the model and guesses the context window wrong.

Every model block is written whole: id, name, reasoning, the input kinds, the context window and its token
count, `maxTokens`, a zero cost and the compatibility flags.
A block with only an id makes OpenClaw lose the context window, and it overflows; when a backend does not
report one, the plan names the model and leaves the field out instead of inventing a number.
A model entry already in the file keeps its fields, and the engine only adds what is missing.

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
  use `nexgen openclaw restart`, which does both and waits for the port.
- Plugins are not changed hot. After a change to `plugins`, restart, and expect the first start to be slow.
- A cold start takes about five minutes. The panel looks dead while it waits; it is not.
- The Companion app from the Microsoft Store installs a second gateway. Two gateways fight over the same port
  and the same state, and `nexgen openclaw doctor` names the Store package when it finds it.

## Installazione con un comando

`nexgen openclaw setup [--yes] [--lan] [--dry-run] [--no-sviluppo]` runs nine steps, in this order, printing
what each one did. It stops at the first step that cannot succeed, and the machine keeps the state it had
before that step.

1. **Installation.** If OpenClaw is missing, or is not on the release `openclaw/pin.json` names:
   `npm install -g openclaw@<pin> --allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw`, with npm
   resolved to its full path (`npm.cmd` on Windows). A machine without Node gets a clear error and the
   download link; the engine never installs a system runtime on its own.
2. **Initial configuration.** With no `openclaw.json`, `openclaw onboard --non-interactive --accept-risk
   --skip-health --no-install-daemon` creates the baseline, and the run continues as if the file had always
   been there. `--skip-health` because the gateway is not started yet; `--no-install-daemon` because the
   engine installs the service itself, and two supervisors for one gateway fight over the same port.
3. **Plan and confirmation.** It prints exactly what `nexgen openclaw plan` prints, then asks. `--yes`
   skips the question; `--dry-run` stops here after printing the plan and the commands it would run.
4. **Safe write**, with the gateway stopped, because OpenClaw does not reload a changed `plugins` block hot:
   - a backup beside the file, `openclaw.json.pre-nexgen-<YYYYMMDD-HHMMSS>`;
   - the sections the engine owns merged into the file in force: dictionaries merged recursively, a key the
     person wrote outside the proposal kept, `plugins.allow` merged as a sorted union, model entries merged
     by `id` so a hand-written model is not dropped;
   - the gateway token generated with `secrets.token_urlsafe(32)` only when there is none, and never
     printed;
   - the engine's guardrail plugin enabled only when its folder is actually deposited;
   - an atomic write, then `openclaw config validate` on the file just written. A failure puts the backup
     back, prints OpenClaw's own error, and exits non-zero.
5. **Agent workspaces.** Each folder is created when missing and the manuals are written only when absent,
   so a manual the person edited is never overwritten. The one exception is a folder onboarding created a
   moment earlier: nothing in it can be the person's yet, so the engine's manual replaces OpenClaw's
   placeholder and its `BOOTSTRAP.md` is removed, because that first-run rite would redefine the agent.
6. **Service.** The gateway is installed as a service when it is not one already, started when the install
   only registered it, and the port is awaited for up to ten minutes, with the elapsed seconds printed: a
   cold start takes about five minutes.
7. **Module.** The host declares `openclaw: local` in `modules.state.yaml`, under its own name.
8. **Phone.** It prints `openclaw qr`, to be run in a terminal of one's own, and `nexgen openclaw pair` for
   the approval.
9. **Doctor.** `doctor.collect()` runs and its summary is printed.

Everything is idempotent: a second `setup` with nothing to change writes no file and restarts no gateway.

To go back, stop the gateway first, copy the backup over the file, then let it start again so the plugins are
reloaded:

```bash
nexgen openclaw stop
cp ~/.openclaw/openclaw.json.pre-nexgen-20261023-120000 ~/.openclaw/openclaw.json
nexgen openclaw restart
```

`nexgen openclaw stop` stops the gateway and waits until the process and the port are gone.
`nexgen openclaw restart [--timeout SECONDS]` stops the gateway, starts it, and waits for the port, printing
the seconds it took; a port that never answers is a non-zero exit, never a silent success.
On Windows this is the only thing that restarts it: OpenClaw's own `gateway restart` does not.

`nexgen openclaw pair` lists the phones waiting for approval. `nexgen openclaw pair --approve <id>` approves
one, asking first unless `--yes` is given. No token is ever printed: `pending()` strips every field whose
name looks like a secret before it reaches the screen.

## Commands

`nexgen openclaw plan [--json] [--lan] [--acpx]` reads the configuration in force, computes what the engine
would write, and prints what it would add, what it would change, what it no longer proposes, and which keys
are the person's and stay. It writes nothing.

`nexgen openclaw doctor [--json]` checks installation, version against the pin, the configuration's validity,
`:latest` ids, caged local models, the heartbeat, the gateway's bind and authentication, Google entries, ACP
seats, agents on subscriptions this machine does not have, whether the gateway is listening, and a second
gateway from the Microsoft Store. `nexgen doctor` runs the same checks as part of its own report, and says
nothing at all on a machine that never installed OpenClaw and never declared the module.

`nexgen openclaw setup` applies that same plan: it installs the pinned release, onboards when there is no
file, merges the owned sections with a backup beside them, writes the agent manuals, installs and starts the
service, declares the module and runs the doctor. What the plan promises is what the setup writes, because
both are computed by the same pure function.

`nexgen openclaw restart` and `nexgen openclaw pair` bring the gateway back and let a phone in; they are
described above, with the rest of the installation. `nexgen openclaw stop` takes it down and waits until the
port is free, which is what a hand edit of `openclaw.json` needs.
