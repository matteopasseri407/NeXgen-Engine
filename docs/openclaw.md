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
The models are tried in that order when the engine chooses one for the agent, so the flat subscription is used before a paid key, and so a local model is used before a cloud one only for the assistant, where latency matters more than depth.
That is a decision taken once, at setup; what happens when the chosen plan runs out is the policy described below.

Each agent gets an `AGENTS.md` in the engine's language: who it is, what it does and does not do, when to
hand work to another agent with `sessions_send`, that the Council is called with `nexgen council`, and that
the Vault is read and written with the `vault-library__*` tools.
Every manual also carries the one write path in so many words: a note is created or changed with
`create_note`, `append_note`, `update_section` or `update_note`, never with `git commit` or `git push` in the
Vault's copy and never by editing its files by hand, because on a server that copy is read-only and a write
made there stays invisible to the other machines.
The code agent also gets `CLAUDE.md` and `.claude/settings.json`, so Claude Code behaves the same way when it
is reached directly.
`nexgen openclaw smoke` is the check a version change owes: it asks OpenClaw for its version, validates the
configuration, probes the gateway, runs `openclaw mcp doctor`, and sends one short turn to an agent. Five lines,
one verdict, and the same command works in a terminal and in a release check.
A gateway machine that was prepared by hand records its role with `nexgen role set server`: the setup does it
by itself on `--server`, and without that record the engine cannot tell the machine is the server, so the
Vault's copy beside it keeps committing in a clone nobody can push from. `nexgen role` prints what is
recorded without changing anything.

Agents may write to each other only along the pairs that exist: `main` with `capo`, `main` with
`automazioni`, and `sviluppo` with both. Without a second agent the block is not written at all, because an
empty allow list means something different from "nobody to talk to".

## The fallback chain

OpenClaw already knows how to move a turn to the next model when a provider answers "this plan is spent";
what it cannot know is that the plan is about to be spent while it still answers.
The engine therefore does two things with the `failover:` section of `03-INFRA/agent-universal-layer/quota.yaml`, and neither of them replaces OpenClaw's own mechanism:

- `nexgen openclaw setup` writes each agent's chain into `agents.entries.<id>.model.fallbacks`, in the
  shape OpenClaw documents (`{primary, fallbacks}`). That is OpenClaw's own failover, unchanged.
- It also writes the same chains, the block line and the maximum age of a reading into the engine-owned
  `nexgen.config.json` beside the plugin, where the governor job reads them on every turn.

With a reading that has passed the line (`block_at`, 95% by default), the governor returns
`providerOverride` and `modelOverride` on `before_model_resolve`, so the turn starts on the next model
instead of running, failing and falling back. It writes one line in the gateway log naming the provider,
the window and the model it chose. It never computes a percentage: the beat leaves them in
`<state dir>/extensions/nexgen/nexgen-quota-state.json`, and a state older than `state_max_age_minutes`
(180 by default) changes nothing. A missing, unreadable or empty policy changes nothing either.

Two rules make this safe to keep in a file the engine does not own:

- a chain you wrote by hand in OpenClaw is never removed. The engine's merge keeps your entries first and
  only adds what is missing, and a proposal that names no chain at all leaves yours exactly as it is;
- the engine writes chains only for the agents it creates. An agent you added in OpenClaw's panel keeps
  its own settings, and `nexgen openclaw doctor` tells you it has no chain instead of editing it.

The plugin needs `plugins.entries.nexgen.hooks.allowConversationAccess: true` for that hook to be
registered at all, since it is not a bundled plugin; the setup writes it together with the enable flag.
It is read at gateway start, so a change there needs the restart the setup already performs.

`nexgen doctor` prints one line, `openclaw.fallbacks`: whether a policy is declared, how many agents carry
a chain, and which agents carry none.

## What the chat can do

The plugin turns the engine's own verbs into commands and tools, and every answer is the text the
engine already rendered. The phone and the terminal therefore cannot disagree.

| From the chat | What it answers | Who may use it |
| --- | --- | --- |
| `/quota` | what is left of each plan, and how old the reading is | anyone |
| `/spend` | what this machine spent, per provider, and what each plan has left | anyone |
| `/council <question>` | starts a council and answers with an identifier; the outcome arrives when the run ends | anyone |
| `/sup` | every supervised task with its units, then the spend of the sessions | anyone |
| `/sup <id>` | that one task | anyone |

Agents reach the same things through tools:

| Tool | What it does | Who may call it |
| --- | --- | --- |
| `nexgen_council`, `nexgen_council_result` | start a council, then read what it produced | any agent |
| `nexgen_sup_status`, `nexgen_sup_costs` | read the supervision and its cost | any agent |
| `nexgen_sup_start`, `nexgen_sup_unit`, `nexgen_sup_run`, `nexgen_sup_collect`, `nexgen_sup_review`, `nexgen_sup_publish` | drive a supervised unit through its steps | the supervisor agent, `capo` |

The six tools that change something are registered `optional`: OpenClaw does not offer them to
any model until an operator allowlists the exact name, and `nexgen openclaw setup` writes that
allowlist on `capo` alone. An agent whose tools the person listed by hand keeps its own
`tools.allow` -- OpenClaw refuses `allow` and `alsoAllow` in the same scope -- and
`nexgen openclaw plan` names the tools to add there instead.

None of this is a second door into the engine: the plugin starts the same CLI a terminal starts,
without a shell, and the guardrail sees every tool call on `before_tool_call` like any other.

A council is the one job that does not answer in the same turn. It runs for minutes, so `/council`
starts it detached and says so at once; the end arrives through the engine's megaphone, which is
the one surface allowed to speak to the person, and the run's own session directory keeps the
whole transcript.

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

The cage limits what a local model can do. What it may claim is a second question, answered below.

## The evidence check

Every answer an agent on a local model gives is compared, before the harness accepts it, with the tool
receipts of the same turn: a read that was not performed, a search that never ran, a command or a test
that never executed, a write that never happened, a cited note that was never opened.
The rules come from the retired local lane and live in the plugin now, so they cover every agent
instead of one action at a time, and they are pure functions of text and receipts.

An answer that does not stand on its receipts gets one more model pass, told exactly which claims have
no evidence. If it comes back the same way, or the run cannot be rewound, the answer is delivered with a
visible note under it. Never dropped in silence, and never a note on somebody else's message: the note
goes on the delivery that carries the judged text.
The claim tables carry English and Italian at once, with no configured language deciding which half is
read: an unsupported English answer is found exactly like an Italian one, and an answer that mixes the two
is judged claim by claim. The note itself is in English, like every other sentence the plugin renders on
its own, because a file with no locale cannot know the language of the person reading.

The switch is not in the plugin: it is the `evidence` section of `nexgen.config.json` beside it, written
by the deposit from the configuration in force. It lists exactly the local providers this machine serves,
so a cloud model is never checked, and a machine with no local model carries an explicit `off`.

The check reads the conversation, which OpenClaw withholds from every non-bundled plugin unless
`plugins.entries.nexgen.hooks.allowConversationAccess` is true. `nexgen openclaw setup` writes that grant,
merged into the `hooks` block already there, and `nexgen openclaw doctor` warns when it is missing.
The hooks themselves are handed to the plugin by OpenClaw, so nothing else has to be installed.

## The jobs that run when nobody is watching

Dreaming, compaction, the memory flush before a compaction and active memory all resolve their model through the
agents' default model when their own key is unset.
On a machine whose default is a local model, the 03:00 dreaming sweep, an automatic compaction and a recall load
that model while nobody is watching, and on a machine without a graphics card that is the whole computer for
minutes.
The person chose a local model for a conversation, not for a sweep.

When the default is local and an agent already runs a cloud model, the setup writes:

| Job | Key | Gate OpenClaw requires |
| --- | --- | --- |
| dreaming | `plugins.entries.memory-core.config.dreaming.model` | `plugins.entries.memory-core.subagent.allowModelOverride: true` |
| compaction | `agents.defaults.compaction.model` | none |
| memory flush | `agents.defaults.compaction.memoryFlush.model` | none |
| active memory | `plugins.entries.active-memory.config.model` | the same gate, on `active-memory` |

The cloud model is the one an agent already runs, `main` first.
`allowedModels` is merged as a set, so an allowlist the person wrote keeps its entries and gains this one.
Nothing is written when the default is already cloud, when no agent has a cloud model, or when the person set that
key: a key that is present is the person's, and an explicit `allowModelOverride: false` is read as a
decision rather than as a gap.
Active memory is written only when that plugin is switched on.

`nexgen openclaw doctor` says in one line which job would still wake a local model.

## The watcher, and the whole home

OpenClaw watches `openclaw.json` for changes.
To do it, it steps one level up from the folder holding the file and watches that, so with the default
`~/.openclaw` the watched folder is the home directory.
On Windows the backend there is one recursive `ReadDirectoryChangesW` handle over the whole tree: every file any
program creates under the home is an event the gateway has to fold.
The engine's own test runs create and delete thousands of them, the queue folds, a reconciliation scan opens a file
another process already deleted, and `EBADF` is not among the errors that library recovers from: after three
attempts OpenClaw turns hot reload off.

So the setup writes `env: { vars: { CHOKIDAR_USEPOLLING: "1" } }` when the state folder puts the home under the
watcher and the platform is Windows.
That is OpenClaw's documented switch: in `poll` mode the native hub is never started and only the configuration
file is polled, every 100 ms by default.
It reaches the gateway process because OpenClaw applies the configuration's own `env` block to `process.env`
during the gateway-run bootstrap, before the reload manager creates the watcher, and it never overrides a value the
process already has.
Nothing is installed, no service is reinstalled, no wrapper script is added, and no user-wide environment variable
is touched.

The cost is real and named: with polling on, the memory index and the skills refresh poll every thirty seconds
instead of receiving events.
On Linux the engine writes nothing. The same one-level step-up happens there, but an entry scope on Linux is one
non-recursive watch per directory, so the home is not walked.

A change of mode takes effect at the next start.
The setup does not cycle the gateway for this key alone: it writes the key, and the ordinary write path restarts
the gateway once, which the plan says when the key is new.
`nexgen openclaw doctor` says whether the machine is in the recursive case, already polling, or polling when
nothing needs it any more.

## Terms of service

OpenClaw uses the person's own subscriptions through the logins that already exist on the machine.
The engine writes no API key of its own there, and no credential value ever enters the configuration it
produces.

No Google plan is configured in OpenClaw: Gemini and Antigravity are allowed only inside Google's own
clients. The engine never writes a `google` plugin, and `nexgen openclaw doctor` fails a configuration that
has one, including an OAuth entry under another name. A `gemini` seat in `acp.allowedAgents` is a warning,
because it is allowed by the protocol and not by the terms.

One road stays open, and it is Google's own client doing the work: a CLI backend plugin that runs the
official `agy` CLI as a local process (OpenClaw documents CLI backends in `docs/gateway/cli-backends.md`
and `docs/plugins/cli-backend-plugins.md`). `agy` keeps the login and the session, OpenClaw holds only the
synthetic marker and never a Google token, and the route is `google-antigravity-cli/*` or an `agentRuntime.id`
with the same id. `nexgen openclaw doctor` gives that backend its own line and names the permission mode the
plugin was given: `skip` (and omitting it, which is the plugin's default) is a warning, because it lets
`agy` approve every tool call by itself, while `sandbox` and `settings` keep an approval boundary.

## Windows, the traps that cost an afternoon

- `openclaw gateway restart` does not restart on Windows. Stop the processes and relaunch the service, or
  use `nexgen openclaw restart`, which does both and waits for the port.
- Plugins are not changed hot. After a change to `plugins`, restart, and expect the first start to be slow.
- A cold start takes about five minutes. The panel looks dead while it waits; it is not.
- An OpenClaw package from the Microsoft Store installs a second gateway. Two gateways fight over the same port
  and the same state, and `nexgen openclaw doctor` names the Store package when it finds it. The Windows Hub app
  described below is a different thing, and the engine recognises it on its own.

## OpenClaw Companion (l'app Windows)

On Windows, OpenClaw provides the **Windows Hub / OpenClaw Companion** app (`OpenClaw.Tray.WinUI`). The companion app supports three distinct gateway modes:

1. **WSL distribution (`wsl`)**: The app provisions a dedicated WSL2 distribution (`OpenClawGateway`), hosting the gateway inside Linux with state under `.openclaw` inside the distribution, communicating on `ws://127.0.0.1:18789`.
2. **Local engine gateway (`engine`)**: The companion connects to the host's native Node gateway managed by NeXgen ("a local Gateway on this PC" in companion settings), sharing loopback and host state.
3. **Isolated native gateway (`isolated`)**: The companion provisions an isolated native MSIX gateway (`OpenClawFoundation.OpenClawGateway`) executing inside an isolated Windows session (`isolated-session-v1`) on a high loopback port (e.g. 52190). The isolated gateway runs under a separate hidden Windows user profile with its own sandboxed workspace and Node runtime; it does not touch WSL, but cannot directly read the main user's files or NeXgen state.

### What this engine does today

It tells these situations apart, from evidence that starts nothing:

| Situation | The evidence it reads | What the engine does |
| --- | --- | --- |
| the native gateway only | the npm package and `~/.openclaw/openclaw.json` | configures it, as always |
| the app installed, naming no gateway | the app's files, with no gateway in them, plus the native package or nothing | configures the native one as above, and at the end says the one step that connects the app: Connections, "a local Gateway on this PC" |
| the app on this PC's gateway | a loopback gateway URL on the engine's own port | one line that says the app is connected, and, when the gateway answers, whether its device is in the gateway's list |
| the app's gateway only (WSL or isolated) | the `OpenClawGateway` distribution, or the isolated native gateway session, or a gateway its settings name on this PC | says what it found (WSL or isolated native) and stops before installing or writing anything |
| both | one of those, plus the native package | configures the native one, and notes the app's gateway as a second gateway in conflict |
| the app connected elsewhere | an `sshTunnel`, or a gateway URL that is not loopback | says so; nothing is in conflict |

- The distribution list comes from `HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss`, which answers without
  starting WSL, and the app's files are read as JSON: `settings.json` for active configuration, `gateways.json` for
  `url`, `isLocal`, `sshTunnel` and `setupManagedDistroName`, and the isolated session package `setup.json`/`gateway.json`
  for native runtime contract evidence. Every field is optional and a file that does not parse says nothing.
- A gateway URL can carry a token in its query string, so it is parsed for its host and its port and then dropped:
  no URL, no token and no device key is ever printed, and a `device-key*` file is never opened.
- The engine never starts the app, WSL or the distribution, and never writes under `%APPDATA%\OpenClawTray`,
  `%LOCALAPPDATA%\OpenClawTray`, the isolated session profile, or `\\wsl.localhost`.

### How doctor, plan and setup interact with each mode

- **Doctor (`nexgen openclaw doctor`)**:
  - `openclaw.companion_gateway`: reports OK when connected to the engine gateway, or when either the isolated native gateway or WSL distribution runs alone as the designated gateway. Warns only when both an engine gateway and an app-managed gateway run simultaneously.
  - `openclaw.store_gateway`: excludes the companion's isolated native gateway MSIX (`OpenClawGatewayMSIX`), preventing false-positive Store package conflict alarms.
- **Plan (`nexgen openclaw plan`)**:
  - Notes the detected companion gateway mode (`isolated`, `wsl`, `engine`, `remote`).
  - Distinguishes isolated native gateway from WSL or remote gateways in proposal summaries.
- **Setup (`nexgen openclaw setup`)**:
  - When an app gateway (WSL or isolated) is active, setup stops and guides switching to the native gateway via `--node-gateway` once the app's gateway is stopped.
  - Setup never modifies, removes, or overwrites isolated session files or companion settings.

### The app's gateway and the native one, when both are wanted

The engine configures the native Node gateway, and that is the road it supports: the app is the interface, the
gateway is this engine's. When the app runs the only gateway here, the setup stops and says so in the plan and
in the run, with the step that fixes it, because a second gateway on the same port helps nobody.

`nexgen openclaw setup --node-gateway` is the way to take that road anyway. Before anything is installed or
written it asks whether the gateway port answers, because two gateways cannot share it:

- nothing answers, so the native gateway is installed, configured and started, and the app is told where to look;
- the app's gateway still answers, so the run stops and says to stop it in OpenClaw Companion and try again.

The app's distribution is never started, stopped or written, with or without the option. The app is not
something to remove, either: the doctor's lines say which gateway is which, never that the app has to go.

### What it does not do yet

- It does not configure the app's gateway: no configuration written through `\\wsl.localhost`, no service, no
  plugin deposited inside the distribution, no pairing inside it.
- It does not install its guardrail plugin there as a package.
- `nexgen openclaw setup` on a machine where the app owns the only gateway stops and names the two ways forward:
  use the app as it is, or install the native gateway and run the setup again. `--node-gateway` goes on with the
  native one once the port is free, which means the app's gateway has been stopped in the app.

Full support, which means talking to the gateway through its own protocol and installing the plugin as a package,
comes in a later release. Until then the two gateways stay two separate things, and the engine only ever writes the
native one.

## Talking to the gateway

The RPC client (`nexgen_core.openclaw.gateway_rpc`) implements the WebSocket JSON-RPC transport (protocol v4)
so the engine speaks to the OpenClaw gateway directly (`ws://127.0.0.1:18789`).

It authenticates as an operator client with the gateway's own token, never printing or logging it, and exposes
one primitive per operation:
- `health()`: the gateway's operational state and the plugins it loaded;
- `config_get()`: the configuration snapshot and the base hash the concurrency check needs;
- `config_patch(patch, base_hash)`: a partial configuration update, protected by optimistic locking;
- `plugins_install(source)` and `plugins_reload(id)`: installing a plugin and reloading it in place.

With this client the engine configures OpenClaw and manages its plugins whatever the hosting environment is (a
native Windows gateway, a WSL gateway managed by OpenClaw Companion, a Linux or a remote one): it never has to
reach into an isolated filesystem, and it never has to restart a process.

One read-only command inspects the RPC connection by hand:
`nexgen openclaw rpc health [--json] [--url URL]`

## The plugin as a package, for the gateways the engine cannot reach

The engine deposits the `nexgen` plugin by copying its files into `<state dir>/extensions/nexgen/`, which
needs a gateway whose filesystem it can write.
The gateway the Companion app runs inside its own WSL distro is not one: `C:` is not mounted there and the
distro belongs to the app, so the engine must not reach into it.
OpenClaw installs a plugin there from an npm tarball, and `nexgen openclaw package` builds that tarball
from the list the deposit itself reads.

It contains the plugin's own files, the shared `nexgen-guardrail-core.mjs` and `vault-briefing.txt`, plus a
`package.json` that carries the engine's version, declares `type: module` and points `openclaw.extensions`
at `./index.mjs`.
The guardrail body, `nexgen-guardrail.config.json` and `nexgen.config.json` stay out: the policy belongs to
the Vault, and this artifact is the part that travels.
The same files produce the same bytes and the same sha256, so a build that changed unnoticed cannot pass for
the one that was reviewed.

The tarball lands in `<state dir>/openclaw/plugin/nexgen-<version>.tgz`.
The engine installs nothing itself: moving it to the gateway host and running
`openclaw plugins install npm-pack:<path.tgz> --force` there is a separate step someone can see.
Without a sidecar beside it the plugin has no policy to run, so it refuses the commands it would judge
instead of allowing them: the package carries the mechanism, and the policy arrives by the route that
gateway allows.

## The MCP bridge, when the gateway runs in WSL

The Windows companion app keeps OpenClaw in an app-owned WSL distro where interop is off
and `C:` is not mounted, so a connector the engine declares as a Windows command
(`python ...\lazy-mcp.py`, `node ...\playwright-human-safe.mjs`) never starts there.

`nexgen mcp bridge start` runs those connectors on the Windows side and serves them as MCP
Streamable HTTP: one endpoint per connector at `http://127.0.0.1:8765/mcp/<name>`, plus
`/mcp` for the gateway itself (`lazy-mcp`). The list of connectors is not written twice: the
bridge asks the renderer what this machine mounts directly in OpenClaw and starts exactly
that, so a connector added to the manifest appears at the bridge on its own. `--server NAME`
narrows it, `--wsl` binds the virtual WSL interface, which is the address a distro on the
default NAT network reaches (with mirrored networking, loopback is enough).

Every request carries `Authorization: Bearer $NEXGEN_MCP_BRIDGE_TOKEN`. The token is
generated into the engine's secrets deposit (`~/.config/nexgen/secrets.env`, 0600) and is
never printed; OpenClaw substitutes the variable itself, so no value enters `openclaw.json`.
Nothing starts the bridge automatically: `nexgen mcp bridge start`, `stop` and `status` are
the whole surface, and `status` exits non-zero when nothing is listening.

`0.0.0.0` is accepted only as an explicit `--host` and only with the token. `stop` asks the
listener to finish and waits for the port; it never kills a process by pid, because the pid
in a stale state file can belong to somebody else by the time it is used.

## Installazione con un comando

`nexgen openclaw setup [--yes] [--lan | --server] [--yes-i-know] [--dry-run] [--no-sviluppo] [--node-gateway]`
runs nine steps, in this order, printing what each one did. It stops at the first step that cannot succeed,
and the machine keeps the state it had before that step.

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
     by `id` so a hand-written model is not dropped, and list-valued leaves such as `deny` and `allowedModels`
     merged as sets;
   - the background-job models when the default model is local, and `env.vars.CHOKIDAR_USEPOLLING: "1"` on
     Windows when the gateway would otherwise watch the home, both described above;
   - the gateway token generated with `secrets.token_urlsafe(32)` only when there is none, and never
     printed;
   - the engine's guardrail plugin enabled only when its folder is actually deposited, and granted
     `hooks.allowConversationAccess`, which both its governor hook and the evidence check need;
   - an atomic write, then `openclaw config validate` on the file just written. A failure puts the backup
     back, prints OpenClaw's own error, and exits non-zero;
   - after OpenClaw has accepted the file, the fallback chains reach the plugin's own
     `nexgen.config.json`, which is read on every turn and needs no restart.
5. **Agent workspaces.** Each folder is created when missing and the manuals are written only when absent,
   so a manual the person edited is never overwritten. The one exception is a folder onboarding created a
   moment earlier: nothing in it can be the person's yet, so the engine's manual replaces OpenClaw's
   placeholder and its `BOOTSTRAP.md` is removed, because that first-run rite would redefine the agent.
6. **Service.** The gateway is installed as a service when it is not one already, started when the install
   only registered it, and the port is awaited for up to ten minutes, with the elapsed seconds printed: a
   cold start takes about five minutes.
7. **Module and bridge.** The host declares `openclaw: local` in `modules.state.yaml`, under its own name,
   and enables the canonical `openclaw` MCP server (`openclaw mcp serve`) in `manifest.yaml` so other harnesses
   can reach OpenClaw tools.
8. **Phone.** It prints `openclaw qr`, to be run in a terminal of one's own, and `nexgen openclaw pair` for
   the approval.
9. **Doctor.** `doctor.collect()` runs and its summary is printed.

After the doctor, on Windows, the run tells the Windows app where the gateway is, and offers the request the
app leaves. An app installed before the engine names no gateway at all: the run says to open Connections in
OpenClaw Companion and choose "a local Gateway on this PC". A request already waiting is printed with its id
and what it says it is, then approved only when the person answers yes at that prompt. `--yes` answers the
plan, never a device approval, and a run with no terminal approves nothing and prints the command instead.
Several requests are listed and none is chosen for the person.

Everything is idempotent: a second `setup` with nothing to change writes no file and restarts no gateway.

### A machine that is always on, with no screen: `--server`

`nexgen openclaw setup --server` is the same nine steps for a Linux machine with no desktop: a VPS, a mini
PC in a cupboard. The gateway stays on `gateway.bind: "loopback"` and OpenClaw publishes it with
`gateway.tailscale.mode: "serve"`, the pair its own manual recommends (`gateway/tailscale.md`,
`gateway/stable-https-url.md`): HTTPS with a real certificate on `https://<host>.<tailnet>.ts.net`, and no
port opened on the machine. The `tailscale` section already in `openclaw.json` is kept; only its `mode`
changes.

Before anything is installed, four questions decide whether this host may carry a gateway:

1. Is it Linux? A desktop is configured without `--server`; `--server` on another system stops and says so.
2. Is Tailscale installed? If not, the run names the download page and stops: the engine never installs a
   system program on its own.
3. Is Tailscale logged in? `tailscale status --json` must report `BackendState: "Running"`; any other state
   stops the run with `tailscale up` as the action.
4. Does the account hold root power? Root itself, `sudo -n true` succeeding, or membership of the `docker`
   group each stop the run, because a gateway that runs agents must not be able to become root through the
   process it supervises. `--yes-i-know` is the explicit way past it, and the run prints what it accepted.

`--server` and `--lan` are opposites and are refused together: `--lan` opens the gateway to the network it
sits on, `--server` keeps it on loopback and lets Tailscale Serve do the publishing.

The service is the systemd user service OpenClaw installs (`openclaw gateway install`), and the lingering
check a Linux host already gets applies here as well: without `loginctl enable-linger <user>` the gateway
stops when the last session closes.

## OpenClaw as an MCP server for other harnesses

OpenClaw can serve its tools to all other harnesses (`claude`, `codex`, `antigravity`, `opencode`) via
`openclaw mcp serve`.
The canonical entry is declared in `03-INFRA/agent-universal-layer/mcp/manifest.yaml` with stdio transport:

```yaml
openclaw:
  exposure: eager
  transport: stdio
  command: openclaw
  args: ["mcp", "serve"]
  targets: [claude, codex, antigravity, opencode]
  enabled: false
```

It is disabled by default (`enabled: false`) and automatically enabled during `nexgen openclaw setup` when
OpenClaw is installed on the machine. The OpenClaw renderer explicitly filters it out, ensuring OpenClaw never
configures itself as an MCP client.

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

A node, another machine rather than a phone, gets in through a second request with an id of its own.
`nexgen openclaw pair` lists those too, and `--approve-node <id>` shows the node's name, its platform and
the commands it would be allowed to run before asking. An id that is not waiting is refused, because an
approval that cannot be described is an approval nobody gave.

## Due PC

One gateway, one node. The gateway is the machine that holds the channels, the cron jobs, the memory and the
model routing; the node is a machine that runs OpenClaw's commands for it. What a person reads still arrives
through the gateway, wherever the command ran.

A channel lives on one gateway at a time. The same PC can run a gateway of its own and be a node of another
gateway, but a Telegram bot cannot answer from both.

### At home: the laptop as a node of the fixed PC

On the fixed PC, once:

1. `nexgen openclaw setup --lan` writes `gateway.bind: "lan"` and a real token. `nexgen openclaw plan --lan`
   shows the same change without writing it.
   Check: `nexgen openclaw doctor` says the gateway listens on the home network.
2. `nexgen openclaw restart` if the gateway was already running, so it binds again.
   Check: the command prints the port answering, and the doctor agrees.

On the laptop, once:

1. `nexgen openclaw setup` installs the pinned release, writes the laptop's own configuration and its own
   loopback gateway. That local gateway is not the one the node talks to, and this setup does not touch it again.
   Check: `nexgen openclaw doctor` passes on the laptop.
2. `nexgen openclaw node --host <address of the fixed PC>` prints the two documented routes, in order, with the
   check after each command. It runs none of them.
   Check: the printed commands are the ones below.
3. Either the one-paste route: `openclaw devices join-code` on the fixed PC, then the command it prints,
   `openclaw connect <url> --service --session-host`, on the laptop. OpenClaw approves the device and its
   first command surface in the same step; `--session-host` also lets the node run agent sessions, so keep it
   to a machine you trust as shared infrastructure. The URL is single use, lasts about ten minutes and
   carries administrator access: treat it like a password and paste it nowhere else.
   Or the explicit route: `openclaw node install --host <address> --port 18789` and `openclaw node start` on the
   laptop, then `nexgen openclaw pair` on the fixed PC to read the device request, `nexgen openclaw pair
   --approve <device id>` to admit it, a reconnect that asks for the commands, and `nexgen openclaw pair
   --approve-node <node request id>` to allow them.
   Check: `openclaw node status --json` on the laptop.
4. On Linux, `loginctl enable-linger <user>` once: without it the systemd user service stops when the last
   session closes. `nexgen doctor` already says when lingering is off.

On the fixed PC, to see the result: `nexgen openclaw doctor --nodes` adds the paired nodes to the report and
checks that the node accepts the commands an agent asks it to run, and `nexgen openclaw pair` lists them with
their state. Neither approves anything on its own.

### A gateway behind Tailscale Serve, and a laptop on the same tailnet

On the always-on machine: `nexgen openclaw setup --server`, then `nexgen openclaw restart` if the gateway was
already running.
Check: `nexgen openclaw doctor` says the gateway listens on loopback and Tailscale Serve publishes it over
HTTPS on the tailnet.

On the laptop: `nexgen openclaw node --host <host>.<tailnet>.ts.net` prints the same two routes and
recognises the MagicDNS name.
The join code carries the HTTPS address the gateway mints, so `openclaw connect <url> --service
--session-host` needs no port and no token of its own; the explicit route is printed as `--port 443 --tls`,
because that port belongs to Serve.
Both machines have to be on the same tailnet and logged in to Tailscale.
Check: `openclaw node status --json` on the laptop, and `nexgen openclaw pair` on the gateway for the
approval.

### Outside the house

The laptop keeps its own gateway in loopback, with no channels. That is what a person reads from when the
fixed PC is out of reach, and it is why the doctor's line about a loopback gateway is information rather than
a fault there: a node on another PC cannot reach it, and on that machine nothing should.

The node connection to the fixed PC exists only while the two machines can reach each other. Approvals are not
lost when it is down, and nothing else is either: memory, channels and cron jobs never lived on the laptop.

### What stays the person's

- The address of the other machine. The engine does not guess it and does not read it from anywhere.
- Both approvals, the device and the commands. Nothing is approved automatically, and every request says which
  machine asks and what it would be allowed to run.
- Which gateway owns a channel. Putting one bot on both gateways is the mistake this page exists to prevent.

## Commands

`nexgen openclaw plan [--json] [--lan | --server] [--acpx]` reads the configuration in force, computes what
the engine would write, and prints what it would add, what it would change, what it no longer proposes, and
which keys are the person's and stay. It writes nothing; `--server` is the read-only preview of the change
`setup --server` applies.

`nexgen openclaw doctor [--json] [--nodes]` checks installation, version against the pin, the configuration's
validity, `:latest` ids, caged local models, whether the evidence check runs and for which local models, the
heartbeat, the gateway's bind and authentication, how the gateway is exposed (loopback, the home network,
every interface, a direct tailnet listener, Tailscale Serve or Funnel, with a failure when a gateway published
through Serve also listens on a network address), Google entries, the local `agy` backend and the permission
mode its plugin was given, ACP seats, agents on subscriptions this machine does not have, which background
job would wake a local model, whether the gateway watches the whole home recursively, whether the gateway is
listening, whether every agent has a fallback chain, a second gateway from the Microsoft Store, the OpenClaw
Companion's own gateway, which it reports without configuring it, and how many skills and servers OpenClaw
created that the canonical library does not declare yet.
When the app is installed, one line answers the two questions a person has: whether the app's settings point at
this PC's gateway, and whether the gateway's own device list knows the app. `nexgen openclaw doctor` reads that
list with `openclaw devices list --json`, on that line only and only when the app says it is on this gateway;
a stopped gateway is a normal state, so the line then says nothing about devices instead of reporting a fault.
`--nodes` adds the nodes the running gateway knows, which costs one run of OpenClaw's CLI and is therefore
never done unless asked; the line about the network needs no query, so it is always there. The same flag reads
each node's own exec approvals for every agent that runs its commands there, and warns when that PC is stricter
than what the agent asks for: a node whose fallback is `deny` answers the agent with `exec denied:
request-failed`, which says nothing about the cause. Without the flag nothing of a node is asked. `nexgen doctor`
runs the same checks as part of its own report, without `--nodes` and without launching that list, and says
nothing at all on a machine that never installed OpenClaw, never declared the module and has no Companion
gateway.

`nexgen openclaw node --host <address> [--port 18789] [--name <label>]` prints the commands that make this
machine a node of a gateway elsewhere: OpenClaw's one-paste route with a join code, and the explicit route with
its two approvals. The address may be a name on the home network or a Tailscale MagicDNS name; the second
needs no port in the one-paste route and `--port 443 --tls` in the explicit one. It writes nothing and runs
nothing, because `openclaw node install` already writes the service on each platform.

`nexgen openclaw setup` applies that same plan: it installs the pinned release, onboards when there is no
file, merges the owned sections with a backup beside them, writes the agent manuals, installs and starts the
service, declares the module and runs the doctor. What the plan promises is what the setup writes, because
both are computed by the same pure function. On a Windows machine it also ends by pointing the app at this
gateway and offering the request the app leaves, in the paragraph that follows the summary the doctor prints.

`nexgen openclaw adopt [--skills] [--mcp] [--dry-run] [--yes] [--auto] [--json]` takes what was created inside OpenClaw and
makes it canonical: the skills the Skill Workshop learned, the servers in `mcp.servers` that no manifest
declares, and ClawHub plugins wrapping MCP servers (detected from plugin manifests `openclaw.plugin.json`,
`package.json`, bundled `mcp.json`/`.mcp.json`, or catalog descriptions matching official/hosted MCP services).
Underlying MCP servers are extracted and proposed for adoption as unmanaged servers, with credentials in `${VAR}`
preserved as references. Installed plugins that are not MCP wrappers (such as channels, memory, or model
providers) are omitted from adoption with a one-line explanatory note. It prints the plan with every check before writing anything. A name already taken is a refusal and
never an overwrite, a plaintext credential blocks, and a skill holding a script, an executable or an unknown
file is a question: `--yes` adopts only what the checks cleared and names what it left. An adopted skill is
copied into `03-INFRA/agent-universal-layer/skills/<name>/`, declared `origin: vault` with `openclaw_id` in the
skills manifest, built into every view and archived in the Workshop through `openclaw skills workshop archive`, so
OpenClaw loads the canonical copy like every other CLI; an adopted server enters the MCP manifest lazily, through
the same writer `nexgen mcp add` uses, stamped with `origin: openclaw` and its OpenClaw identifier.
Canonical resources without a provenance mark are never removed by adoption.

The mark differs per manifest because the key `origin` already means something else in the skills one: there it
names where the bytes are materialized from (`engine`, `vault`, `github`, `installer`, `upstream`), and an adopted
skill is a vault skill -- the copy lives in the Vault like any other. Its OpenClaw provenance is the `openclaw_id`
it carries, and the skill's own `SKILL.md` frontmatter records the same pair; the MCP manifest had no such key, so
`origin: openclaw` is the mark there. Either way, an entry without a mark belongs to the library and this
mechanism never touches it.

When executed with `--auto`, adoption runs non-interactively: resources verified as SAFE enter the canonical library
immediately, while everything else -- a resource that needs a person and one the checks refuse -- stays outside and
is recorded in machine-local state (`openclaw-adopt-state.json`, under the engine's state directory, never the Vault
and never the repository) with the reason, for the plugin to show. In addition, `--auto` tracks absences for MCP
servers: a server adopted from OpenClaw and absent from it for two consecutive passes is retired from the manifest,
listed under `retired_servers`, and unmounted by the next sync. A single missed pass, a broken configuration or an
unreachable gateway removes nothing and says so, and a retirement whose write fails is reported as a failure, never
as an exit.

Skills are admitted by the same pass and are **not** retired by absence, because absence there means nothing: an
adopted skill was copied into the Vault and its Workshop original archived, which is exactly what adoption does, so
a poll of the Workshop would find it gone and delete the copy the library had just taken in. A skill leaves the
library when its manifest entry is removed, which is a person's edit and not a side effect of a timer. A server is
different: `openclaw.json` is the person's file, its `mcp.servers` table is the honest record of what OpenClaw has,
and the engine never writes that table. A name already listed under `retired_servers:` is not adopted back: remove
it from that list first, the same rule `nexgen mcp add` states, so bringing a server back stays a decided act.

The OpenClaw `nexgen` plugin registers `/adopt` to run a pass from chat and to show what is waiting, and the
`doctor` prints one line for the last pass: what entered, what left, and what waits for a person.

The pass runs from three places, all of them in the plugin that OpenClaw already loads: `/adopt` in chat, the
`skill_changed` hook (the only lifecycle event OpenClaw announces for what the Workshop and ClawHub create), and
the gateway's own clock -- one bounded pass every 30 minutes while the gateway is up, the same cadence as the
engine's guard cycle, armed on `gateway_start` and cleared on `gateway_stop`, one pass at a time. That is what
covers a removed MCP server: OpenClaw's hook catalog (`docs/plugins/hooks/reference.md` in the installed release)
has no equivalent for `mcp.servers`, so the tick is the pass that notices. A tick that could not start the engine
writes the sentence to the gateway log; a skill change stays quiet, so the Workshop's own notice reads alone.


`nexgen openclaw package [--json]` builds the plugin's npm tarball in the engine's state folder and prints
the file and its sha256, which is the pair an install on another gateway is checked against. It installs
nothing and writes nothing else; `--json` reports the same path and hash with the files inside.

`nexgen openclaw restart` and `nexgen openclaw pair` bring the gateway back and let a phone, or another
machine, in; they are described above, with the rest of the installation. `nexgen openclaw stop` takes it down
and waits until the port is free, which is what a hand edit of `openclaw.json` needs.

`nexgen openclaw rpc health [--json] [--url URL]` inspects the gateway's operational state through the
WebSocket `health` call, reporting uptime and the plugins it loaded without changing anything.