# OpenClaw, the bot that stays on

🇮🇹 [Italiano](it/openclaw.md)

OpenClaw (MIT, npm `openclaw`) is the engine's always-on bot. It runs on the machine that never sleeps, it is the one a person reaches from the chat channels they already use, Telegram among them, and it carries an agent squad declared in `squad.yaml` with the channel bindings that route a chat to the right agent. It sits beside the apps a person works in day to day, which the sync configures, and it does not replace them. Its web panel and phone app, paired by QR, use the same flat subscriptions a person already pays for and the models running on their own machine. The sync also writes it as a fifth CLI, which is the smaller half of its job.

The engine does not fork OpenClaw and does not own its file. It writes only the sections OpenClaw needs to reach the subscriptions and the Vault, and leaves everything else to OpenClaw and to the person using it.

## What the engine detects

`nexgen openclaw plan` and `nexgen openclaw doctor` read this list and nothing else:

| What | How |
| --- | --- |
| OpenClaw | the global npm package's `package.json`, located with `npm root -g`; the version must match `agent-universal-layer/openclaw/pin.json` |
| The configuration | `$OPENCLAW_CONFIG_PATH`, else `$OPENCLAW_STATE_DIR/openclaw.json`, else `~/.openclaw/openclaw.json` |
| Claude | the `claude` binary on PATH and `~/.claude/.credentials.json`. On macOS the login lives in the Keychain: the engine reports "not verifiable" and trusts the binary |
| Codex | the `codex` binary on PATH and `auth.json` under `$CODEX_HOME`, or `~/.codex` |
| OpenCode Go | a key counts as available if `OPENCODE_API_KEY` is set, if OpenClaw already holds an `opencode-go` auth profile, or if OpenCode itself is logged in for it. OpenCode's `auth.json` is read for its top-level key names only, never for a value |
| Ollama | `GET $OLLAMA_HOST/api/tags`, default `http://127.0.0.1:11434`, then `POST /api/show` per model for its context window and capabilities, with short timeouts |
| llama.cpp | `GET $NEXGEN_LLAMACPP_URL/v1/models`, default `http://127.0.0.1:8080`, and `/props` for the context window |
| n8n | the module declared `local` in the engine, with its environment gates set |

The engine never opens a credential file to read a value. The one deliberate exception is OpenCode's `auth.json`, parsed to see which providers hold a top-level key so an existing `opencode-go` login can be reused; no value is copied anywhere. The two local backends are probed through an injectable client, so a machine without them pays one short timeout and no more.

The engine never downloads a model. When it finds none, the plan prints what to start and stops there.

## The agents

The engine creates one agent and only one: `main`, the assistant that talks to the person, finds things in the Vault and hands work on. `main` is both the id and the name the engine gives it, and the engine invents no other: a squad that renames it with `name:` changes what the interface shows, never the id. It runs the first model this machine can reach, in this order: `opencode-go/deepseek-v4.1-flash`, a local model, Codex, Claude. A team is the person's, and `squad.yaml` is where it is declared (see [squad.md](squad.md)). An install that already carries `capo`, `automazioni` or `sviluppo` keeps them, and keeps the supervision tools on `capo`, so an update never moves a permission behind the person's back.

A squad adds an agent by naming it. Each one gets an `AGENTS.md` in the engine's language: who it is, what it does and does not do, when to hand work to another agent with `sessions_send`, that the Council is called with `nexgen council`, and that the Vault is read and written with the `vault-library__*` tools.

An agent that declares a machine gets a different manual. `node: "MatteoPC"` pins the agent's shell to that paired machine, and the manual says what that job is: report what runs there, open supervised work with `nexgen-local sup`, enter a CLI that is already open, say so when the machine is off, and keep the state on the machine. The org chart names the machine each agent controls, which is how the assistant knows whom to hand that PC's work to. See [squad.md](squad.md#node-execution-delegation).

Whom it reports to and who reports to it is a second delimited block in that file, titled `Organigramma` (or `Org chart`), written by both the setup and `nexgen sync` from `reportsTo`. The shipped manual is the same on every machine, so the roster cannot live in its body.

Every manual also states the one write path in so many words: a note is created or changed with `create_note`, `append_note`, `update_section` or `update_note`, never with `git commit` or `git push` in the Vault's copy and never by editing its files by hand. On a server that copy is read-only, so a write made there stays invisible to the other machines.

The code agent also gets `CLAUDE.md` and `.claude/settings.json`, so Claude Code behaves the same way when it is reached directly.

`nexgen openclaw smoke` is the check a version change owes. It asks OpenClaw for its version, validates the configuration, probes the gateway, runs `openclaw mcp doctor`, sends one short turn to an agent, and verifies the first-prompt context budget: six lines, one verdict, the same in a terminal and in a release check.

The first-prompt ceiling measures the actual token cost paid on the first prompt of a session, read directly from local transcripts (Claude Code under `~/.claude/projects/` or `$CLAUDE_CONFIG_DIR`, Codex under `~/.codex/sessions/` or `$CODEX_HOME`). The transcript is the one the turn itself started, dated by its first record: a chat that happens to be writing meanwhile is somebody else's and is never measured. If a session starts over budget, `smoke` and `nexgen doctor` (`openclaw.context_budget`) fail, and a day with no OpenClaw session is not a finding at all. The version gate is the one place where this number does not decide: it prints a warning, because a large chat says nothing about whether a new OpenClaw works, and a rollback for a token count is not a verdict on a release. The default ceiling is 150,000 tokens (calibrated against real measurements: ~13.5k-78k tokens with loopback denies in place, versus ~945k without them), overridable via the environment variable `NEXGEN_OPENCLAW_FIRST_PROMPT_BUDGET`. The `agy` CLI is not measured in tokens because it writes no local session transcript or usage file that can be trusted.

The seed is not the bill: a turn pays it on every model call. `nexgen doctor` (`openclaw.turn_budget`) therefore also groups the newest session of each configured agent into turns and reports, per turn, how many model calls it made and what they cost in input, cache read, cache write and output. A turn over the per-turn ceiling (2,000,000 tokens, `NEXGEN_OPENCLAW_TURN_BUDGET`) is a warning that names the worst turn, its call count and the agent, because a session that passes at 78,717 tokens and then makes 74 calls spends 5.8 M and used to look green. A prompt opens a turn; a tool result does not, so the 74 calls of one turn are not read as 74 turns. The folders come from the configuration in force: an ACP agent is measured in the `cwd` its runtime declares, every other agent in its `workspace`, and `agy` stays exempt for the reason above. Codex is measured by its first prompt only. The same doctor prints `openclaw.harness_lane` (which lane each agent's turn really runs on: `acp`, `cli` or `provider`, warning when a bound agent spends a paid plan on the provider lane) and `openclaw.acp_binding` (an ACP runtime with no binding behind it answers a direct chat with the default model).

What a session starts with is written down too. `nexgen openclaw setup` proposes `agents.defaults.bootstrapMaxChars` 20000, `agents.defaults.bootstrapTotalMaxChars` 60000 and `skills.limits.maxSkillsPromptChars` 18000, the defaults OpenClaw 2026.11.2 documents and ships, and never overwrites a value already in the file; `openclaw.prompt_budget` warns when a limit is shorter than the engine's pointer block. Two more lines guard what the lanes depend on: `openclaw.skills.root` fails when `OPENCLAW_STATE_DIR` points OpenClaw away from `~/.openclaw`, where it would leave the shared `~/.agents/skills` out of every session, and `openclaw.acp.acpx` reads the installed `acpx` plugin and warns when it is missing, older than the release this engine was verified on, or lacks the adapter a harness in `acp.allowedAgents` needs. A node is silent on the skill root, because its skill index belongs to the gateway.

A gateway machine prepared by hand records its role with `nexgen role set server`. The setup does this by itself on `--server`. Without that record the engine cannot tell the machine is the server, and the Vault's copy beside it keeps committing in a clone nobody can push from. `nexgen role` prints what is recorded and changes nothing.

Agents may write to each other freely: the engine does not write `tools.agentToAgent.allow` at all, and OpenClaw reads an omitted list as every pair allowed. A squad that wants to narrow one agent declares `tools.agentToAgent.send` for that agent alone. Who delegates to whom, and who reports to whom, is the hierarchy each manual carries from `reportsTo` (see [squad.md](squad.md#reportsto)).

## The fallback chain

OpenClaw already moves a turn to the next model when a provider answers "this plan is spent". What it cannot know is that the plan is about to be spent while it still answers. The engine therefore does two things with the `failover:` section of `03-INFRA/agent-universal-layer/quota.yaml`, and neither replaces OpenClaw's own mechanism:

- `nexgen openclaw setup` writes each agent's chain into `agents.entries.<id>.model.fallbacks`, in the shape OpenClaw documents (`{primary, fallbacks}`). That is OpenClaw's own failover, unchanged.
- It also writes the same chains, the block line and the maximum age of a reading into the engine-owned `nexgen.config.json` beside the plugin, where the governor job reads them on every turn.

When a reading has passed the line (`block_at`, 95% by default), the governor returns `providerOverride` and `modelOverride` on `before_model_resolve`, so the turn starts on the next model instead of running, failing and falling back. It writes one line in the gateway log naming the provider, the window and the model it chose. It never computes a percentage: the beat leaves the numbers in `<state dir>/extensions/nexgen/nexgen-quota-state.json`, and a state older than `state_max_age_minutes` (180 by default) changes nothing. A missing, unreadable or empty policy changes nothing either.

An ACP session is the harness running as itself, so OpenClaw's chains are never consulted for it. A squad that writes `acp_admission: true` also gets that lane gated: on `before_agent_reply` the same job reads the same readings and, when the plan the agent's model names is at or past the line, answers one line naming the plan and the window instead of starting the harness turn. It costs no model call, and nothing happens for a lane the state has no reading for. See [squad.md](squad.md#acp_admission-true).

Two rules make this safe in a file the engine does not own:

- a chain written by hand in OpenClaw is never removed. The merge keeps your entries first and only adds what is missing, and a proposal that names no chain leaves yours exactly as it is;
- the engine writes chains only for the agents it creates. An agent added in OpenClaw's panel keeps its own settings, and `nexgen openclaw doctor` tells you it has no chain instead of editing it.

The plugin needs `plugins.entries.nexgen.hooks.allowConversationAccess: true` for that hook to be registered at all, since it is not a bundled plugin. The setup writes it together with the enable flag. OpenClaw reads it at gateway start, so a change there needs the restart the setup already performs.

`nexgen doctor` prints one line, `openclaw.fallbacks`: whether a policy is declared, how many agents carry a chain, and which agents carry none.

## What the bot spent

`nexgen openclaw cost [--json] [--since 2026-11-14]` answers the other half of the spend question: not what one turn cost, but what the bot spent per day, per session and per agent, read from the transcripts OpenClaw already wrote.

The reading is [ccusage](https://github.com/ccusage/ccusage) (MIT), an optional binary on `PATH`. It merges the per-message JSONL with the per-agent `openclaw-agent.sqlite` that OpenClaw writes from 2026.9.x, where the provider-billed cost lives, and it uses the cost OpenClaw embedded, so nothing is priced here and no network or credential is touched. A machine without it sees exactly what it saw before.

`daily` and `session` group by date and by session id only, so the per-agent split is the engine's half of the job: a session id is matched against the file name OpenClaw writes under `<state dir>/agents/<agentId>/sessions/`, and the folder that holds it names the agent. A transcript deleted whole is reported as unattributed rather than guessed. A session counts whole, on the day of its last activity, because the session view carries two activity stamps and no per-day split; the day totals themselves are exact.

The answer is cached for ten minutes in the machine-local state directory and the run starts there, so a command, a doctor and a second command in the same interval pay for one run. A missing binary, a non-zero exit, a timeout or an output shape this engine does not recognise costs one line and nothing else: the output has no schema version, so a release that renames a field is refused whole instead of printing the number it still understood. `backends: {ccusage: off}` in `quota.yaml`, or `NEXGEN_OPENCLAW_CCUSAGE=off` for a single run, turns it off.

`nexgen openclaw doctor` adds one line from the same cache, saying what yesterday cost per agent. It is never red: a bill says nothing about whether the machine works, and the scheduled `nexgen doctor` does not ask, because a schedule has no business starting a third-party binary.

## What a harness costs inside OpenClaw

An agent that runs a harness through OpenClaw has to cost, per model call, what the same harness costs when it is run directly, plus a light OpenClaw overhead. The owner set that tolerance at 10,000 input tokens per call, and no release ships until it has been measured and passed. `nexgen openclaw parity [--lane LANE] [--json] [--dry-run]` is the measurement.

It reads `openclaw.json` through the same lane resolver the doctor uses, which means the lanes it knows are exactly the ones the file really runs: `google-antigravity-cli` (`agy`), `claude-cli` and `codex-cli`. Provider lanes run no harness, so their row says "not applicable", and so does a lane no configured agent uses; an ACP session is not measured either, because it runs the harness as itself on its own prompt rather than adding a second prompt to the harness's call.

One agent per lane is measured -- the first the file lists -- and it costs two model calls, which is why nothing runs this on a schedule. The probe message is the same on both sides and short on purpose (`Rispondi solo con la parola OK.`), the native command runs in that agent's own workspace, and the OpenClaw side asks for a fresh `--session-key` so a resumed chat cannot make either number look better:

```sh
agy -p "Rispondi solo con la parola OK." --output-format json --model gemini-3.8-flash
claude -p "Rispondi solo con la parola OK." --output-format json --model claude-opus-5-5
codex exec --json --model gpt-5-2-codex "Rispondi solo con la parola OK."
openclaw agent --agent main --session-key parity-google-antigravity-cli-<epoch> --message "..." --json
```

What is compared is the **total** input of that one call: `input` plus the cache read and the cache write, on both sides, so a warm prompt cannot pass for a cheap one. Anthropic-shaped usage (Claude, and `agy`) keeps the cache out of `input_tokens`, so both are added; Codex prints OpenAI's shape, where `cached_input_tokens` is a subset of `input_tokens`, and adding it again would report a number that is not a bill. The overhead is `openclaw - native`, and a lane passes up to `PARITY_OVERHEAD_TOKENS` (10,000). The measured `agy` lane on 2026-11-15 was 18,307 natively against 25,417 through OpenClaw: 7,110 of overhead on OpenClaw 2026.11.2 and `agy` 1.5.9.

The record is `99-INDEX/OPENCLAW-PARITY.json` in the Vault: the engine commit, the OpenClaw and harness versions, when it was measured, and one row per lane with the two totals, the overhead and the verdict. It is written where the measurement runs and published from a machine that may publish the Vault. The gateway on a server is not that machine: the server role reads the Vault, and the vault-library writes Markdown notes only, so a JSON record cannot travel that road. There the command keeps the record in the machine's state directory and prints the path, and the file is copied into the Vault from the machine that owns it -- the same rule `nexgen update` follows for the engine pin. `--dry-run` prints the two commands per lane and spends nothing.

`nexgen openclaw doctor` reports it as `openclaw.parity.tokens` on the gateway only: green with the age of the record and the worst overhead it measured, a warning when there is no record, it is older than seven days, or it was measured on another OpenClaw version, and red when a lane failed. `release.py preflight` -- which `nexgen release ship` runs itself -- refuses a release when the Vault has no record, a lane failed, the record is older than seven days, or it names an engine commit that is not part of the release being tagged.

## What the chat can do

The plugin turns the engine's own verbs into commands and tools, and every answer is text the engine already rendered. The phone and the terminal therefore cannot disagree.

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

The six tools that change something are registered `optional`: OpenClaw offers them to no model until an operator allowlists the exact name, and `nexgen openclaw setup` writes that allowlist on `capo` alone. An agent whose tools the person listed by hand keeps its own `tools.allow`, because OpenClaw refuses `allow` and `alsoAllow` in the same scope, and `nexgen openclaw plan` names the tools to add there instead.

None of this is a second door into the engine. The plugin starts the same CLI a terminal starts, without a shell, and the guardrail sees every tool call on `before_tool_call` like any other.

A council is the one job that does not answer in the same turn. It runs for minutes, so `/council` starts it detached and says so at once. The end arrives through the engine's megaphone, the one surface allowed to speak to the person, and the run's own session directory keeps the whole transcript.

## Which lane runs an agent

`harness` on an agent in `squad.yaml` is the whole switch, and it is opt-in: an agent without the key keeps the lane it has today, so no setup moves an existing agent anywhere.

| `harness` | Lane | What runs the turn | What it costs |
| --- | --- | --- | --- |
| absent, or `none` | provider | OpenClaw itself, calling the model API it holds a key for | one turn of OpenClaw's own prompt, on that flat plan or key |
| `opencode`, `claude`, `codex` | ACP session | the real CLI, as its own agent, in that agent's workspace, on its own login | the harness's own context on the harness's own plan; OpenClaw keeps channels, delivery and policy |
| `agy` | CLI backend | the official `agy` CLI, through the `google-antigravity-cli` plugin | OpenClaw's prompt plus the loopback tool surface |

An ACP lane is written as both halves or not at all: the `agents.entries.<id>.runtime` block and a `bindings` entry of `type: "acp"`, so an agent the key names and no binding routes a conversation to stays where it is instead of answering a direct chat with the default model, and `acp.allowedAgents` names only the harnesses this machine can really run, chosen from `claude`, `codex` and `opencode` (`gemini` is never written: Google's terms do not allow its client outside Google's applications). The `acpx` plugin goes in `plugins.allow` with `permissionMode: "approve-all"` and `nonInteractivePermissions: "deny"`, because a non-interactive session has no way to click a permission prompt and must either proceed or deny, and `agy` is never an ACP session: it is a CLI backend on Google's own client. `nexgen openclaw plan` names every case it cannot write, `nexgen openclaw doctor` prints each agent's lane, and `--no-sviluppo` leaves the OpenCode agent out even when both prerequisites are there.

## The cage around a local model

A local model in OpenClaw can reach the same tools as any other, and a 12B model with a shell is a bad idea. Every local model therefore gets an entry in `tools.byProvider["<provider>/<id>"]`:

- `profile: "messaging"`;
- `alsoAllow`: `read`, `view_image`, `pdf`, `tts`, `memory_search`, `memory_get`;
- `deny`: the `group:web`, `group:runtime`, `group:ui`, `group:nodes` and `group:automation` families, `write`, `edit`, `apply_patch`, `playwright__*`, `lazy-mcp__lazy_mutate`, and the writing tools of every MCP server that has them.

Writing tools are named `server__tool` in OpenClaw, so the deny list comes from one table in `profile.py` rather than being scattered through the code. Today the table covers `n8n-mcp` (create, delete, update, publish, execute, test and the rest), `vault-library` (create, append, update, section), `drive` (the two upload steps) and `firecrawl` (scrape, map, extract, crawl, agent, browser, interact; search and reads stay allowed).

Ollama ids are written exactly as `/api/tags` returns them, without the `:latest` suffix. With the suffix OpenClaw does not recognise the model and guesses the context window wrong.

Every model block is written whole: id, name, reasoning, the input kinds, the context window and its token count, `maxTokens`, a zero cost and the compatibility flags. A block with only an id makes OpenClaw lose the context window, and it overflows. When a backend does not report a window, the plan names the model and leaves the field out instead of inventing a number. A model entry already in the file keeps its fields, and the engine adds only what is missing.

The cage limits what a local model can do. What it may claim is a second question, answered below.

## The evidence check

Before the harness accepts an answer from a local model, the plugin compares it with the tool receipts of the same turn: a read that was not performed, a search that never ran, a command or a test that never executed, a write that never happened, a cited note that was never opened. The rules come from the retired local lane and live in the plugin now, so they cover every agent instead of one action at a time, and they are pure functions of text and receipts.

An answer that does not stand on its receipts gets one more model pass, told exactly which claims have no evidence. If it comes back the same way, or the run cannot be rewound, the answer is delivered with a visible note under it. It is never dropped in silence, and the note never lands on somebody else's message: it goes on the delivery that carries the judged text. The claim tables carry English and Italian at once, with no configured language deciding which half is read, so an unsupported English answer is found exactly like an Italian one and a mixed answer is judged claim by claim. The note itself is in English, like every other sentence the plugin renders on its own, because a file with no locale cannot know the language of the person reading.

The switch is not in the plugin. It is the `evidence` section, written by the deposit from the configuration in force. It lists exactly the local providers this machine serves, so a cloud model is never checked, and a machine with no local model carries an explicit `off`. It reaches the plugin through `plugins.entries.nexgen.config` (what OpenClaw hands over as `api.pluginConfig`) and, for one more release, through `nexgen.config.json` beside it; the doctor reads the same two places in the same order.

The check reads the conversation, which OpenClaw withholds from every non-bundled plugin unless `plugins.entries.nexgen.hooks.allowConversationAccess` is true. `nexgen openclaw setup` writes that grant, merged into the `hooks` block already there, and `nexgen openclaw doctor` warns when it is missing. OpenClaw hands the hooks themselves to the plugin, so nothing else has to be installed.

## The jobs that run when nobody is watching

A bot that stays on runs three kinds of work nobody types: automation jobs, the background jobs of the memory plugin, and the conversation itself. Each one is bounded here, and each one keeps a way back to the older, more expensive behaviour.

### The proactive check

A heartbeat is an ordinary automation job in the pinned release. `agents.defaults.heartbeat` is retired: OpenClaw's own doctor converts that key into a job and deletes it, and an explicit setup provisions one enabled **Proactive check** per ambient owner agent, every 30 minutes, in the agent's main conversation, delivering to the owner DM. That is 48 model calls a day over everything that conversation has grown to, and this squad's own agent pays 78,717 tokens to open one call (`docs/proposals/daru-burn.md`). The engine used to write `every: 0m` and its doctor read it back, which was a green line over a switch the release does not read: the job fires whatever the file says.

The setup therefore converges the job itself, through the supported CLI, after the gateway is up. The default keeps the capability, the run, and its delivery, and removes what makes it expensive:

| The check you want | What `nexgen openclaw setup` runs |
| --- | --- |
| cheap (the default) | `openclaw automations edit <id> --every 6h --session isolated --light-context --tools "" --model <utility model>` |
| the full one | `heartbeat: mode: full` in `squad.yaml`: the job stays as OpenClaw provisioned it, and `every`/`model` are applied when the block names them |
| none | `heartbeat: mode: off`: `openclaw automations disable <id>` |

`--light-context` skips the workspace bootstrap files and is documented for isolated jobs only, which is why the session moves with it: a check left in the main conversation re-sends everything that conversation has grown to. `--tools ""` disables every agent tool for the run. The model is the utility model the same setup writes just below, so a check and a chat title cost the same cheap call instead of a turn on the primary model. Four light runs a day are a few thousand tokens against 3.8 million for 48 runs of 78,717.

A check of your own is one line in `squad.yaml`, and the setup converges the job to it:

```yaml
heartbeat:
  mode: full        # light (the default) | full | off
  every: 6h
  model: ollama/qwen3:8b
```

A scheduler that does not answer is never assumed converged: the line says so, and `nexgen openclaw doctor` reports it as `unknown` when nothing asked it. When something did ask, the doctor judges the job rather than its name: green only for a check that runs in its own session, without the bootstrap files and without tools, at four runs a day or fewer, and a warning that names the 78,717-token cost when it still runs over the agent's own conversation.

### The calls nobody types

Generated session and channel titles, progress narration, activity recaps, transcript summaries and side chat are separate model calls. Without `agents.defaults.utilityModel` every one of them is billed to the agent's primary model, which is how a five-hour window pays for a label.

The setup writes that key to the cheapest model this machine has, in this order: a local model it serves, then the flat Go key's flash model, then Codex. It never proposes a Claude model, because that window is the thing being protected, and it writes nothing when the file already answers, including the empty string that switches utility routing off. A backend that is not running costs one retry on the primary model, which is OpenClaw's own rule for an exhausted utility chain, so a stopped local server slows a title down and never removes it.


### Dreaming

Upstream runs a full dreaming sweep every night at 03:00 unless somebody stops it (`plugins.entries.memory-core.config.dreaming.enabled`, default `true`, cadence `0 3 * * *`), and one sweep is a light, REM and deep phase over the recent notes and sessions. The engine writes `frequency: "0 3 * * 0"` where the file states no cadence, so the memory consolidation still happens and is paid for on one night in seven. Keeping the nightly sweep is a key of your own: `enabled: true` with upstream's cadence, or any `frequency` you write, is never overwritten. `enabled: false` is the other answer, and it removes nothing else: the memory plugin keeps working.

Compaction, the memory flush before a compaction and active memory resolve their model through the agents' default model when their own key is unset. On a machine whose default is a local model, an automatic compaction and a recall load that model while nobody is watching, and on a machine without a graphics card that is the whole computer for minutes. The person chose a local model for a conversation, not for a sweep.

When the default is local and an agent already runs a cloud model, the setup writes:

| Job | Key | Gate OpenClaw requires |
| --- | --- | --- |
| dreaming | `plugins.entries.memory-core.config.dreaming.model` | `plugins.entries.memory-core.subagent.allowModelOverride: true` |
| compaction | `agents.defaults.compaction.model` | none |
| memory flush | `agents.defaults.compaction.memoryFlush.model` | none |
| active memory | `plugins.entries.active-memory.config.model` | the same gate, on `active-memory` |

The cloud model is the one an agent already runs, `main` first. `allowedModels` is merged as a set, so an allowlist the person wrote keeps its entries and gains this one. Nothing is written when the default is already cloud, when no agent has a cloud model, or when the person set that key: a key that is present is the person's, and an explicit `allowModelOverride: false` is read as a decision rather than as a gap. Active memory is written only when that plugin is switched on.

`nexgen openclaw doctor` says in one line which job would still wake a local model.

### One conversation that never rolls over

OpenClaw's own session policy is `reset.mode: none`: DMs share the agent's main session and the only bound on it is compaction, which is a model call over the whole context every time it runs. A chat at a hundred thousand tokens makes every message a large prompt, and the proactive check above pays it too.

A machine that answers on a channel therefore gets:

```json5
{ "session": { "reset": { "mode": "idle", "idleMinutes": 1440 } } }
```

One day of silence, then the next message starts a fresh conversation from the agent's memory files, which is where the durable half of a chat already lives. It is not abrupt: nothing is dropped while somebody is talking, and background writes (a cron wakeup, an automation job) do not renew the window, so an idle bot cannot hold a conversation open by itself. A `reset`, `resetByType` or `resetByChannel` already in the file is the person's and is never replaced. A machine with no channel gets nothing: one person talking to their own assistant is not the cost this is aimed at. `runtime.dm_scope` in `squad.yaml` splits a DM from the panel when the two should not share a conversation at all.

Tool Search stays on, because it is what keeps the connector schemas out of every request: the engine never writes `tools.toolSearch`, and a legacy `tools.toolSearch: false` written by an older setup is retired on the next run. `nexgen openclaw doctor` prints both facts.


## The watcher, and the whole home

OpenClaw watches `openclaw.json` for changes. To do it, it steps one level up from the folder holding the file and watches that, so with the default `~/.openclaw` the watched folder is the home directory. On Windows the backend there is one recursive `ReadDirectoryChangesW` handle over the whole tree: every file any program creates under the home becomes an event the gateway has to fold. The engine's own test runs create and delete thousands of them, the queue folds, a reconciliation scan opens a file another process already deleted, and `EBADF` is not among the errors that library recovers from. After three attempts OpenClaw turns hot reload off.

The setup therefore writes `env: { vars: { CHOKIDAR_USEPOLLING: "1" } }` when the state folder puts the home under the watcher and the platform is Windows. That is OpenClaw's documented switch: in `poll` mode the native hub is never started and only the configuration file is polled, every 100 ms by default. It reaches the gateway process because OpenClaw applies the configuration's own `env` block to `process.env` during the gateway-run bootstrap, before the reload manager creates the watcher, and it never overrides a value the process already has. Nothing is installed, no service is reinstalled, no wrapper script is added, and no user-wide environment variable is touched.

The cost is real and named: with polling on, the memory index and the skills refresh poll every thirty seconds instead of receiving events. On Linux the engine writes nothing. The same one-level step-up happens there, but an entry scope on Linux is one non-recursive watch per directory, so the home is not walked.

A change of mode takes effect at the next start. The setup does not cycle the gateway for this key alone: it writes the key, and the ordinary write path restarts the gateway once, which the plan says when the key is new. `nexgen openclaw doctor` says whether the machine is in the recursive case, already polling, or polling when nothing needs it any more.

## Terms of service

OpenClaw uses the person's own subscriptions through the logins that already exist on the machine. The engine writes no API key of its own there, and no credential value ever enters the configuration it produces.

No Google plan is configured in OpenClaw: Gemini and Antigravity are allowed only inside Google's own clients. The engine never writes a `google` plugin, and `nexgen openclaw doctor` fails a configuration that has one, including an OAuth entry under another name. A `gemini` seat in `acp.allowedAgents` is a warning, because the protocol allows it and the terms do not.

One road stays open, and Google's own client does the work: a CLI backend plugin that runs the official `agy` CLI as a local process (OpenClaw documents CLI backends in `docs/gateway/cli-backends.md` and `docs/plugins/cli-backend-plugins.md`). `agy` keeps the login and the session, OpenClaw holds only the synthetic marker and never a Google token, and the route is `google-antigravity-cli/*` or an `agentRuntime.id` with the same id. `nexgen openclaw doctor` gives that backend its own line and names the permission mode the plugin was given: `skip` (and omitting it, which is the plugin's default) is a warning, because it lets `agy` approve every tool call by itself, while `sandbox` and `settings` keep an approval boundary.

### The `agy` backend: the plugin, and what `agy` itself needs

The plugin is third-party: `google-antigravity-provider`, MIT, from `github.com/jason-allen-oneal/openclaw-google-antigravity-provider`. It is published on ClawHub and installed at one exact release, never at `@latest`. `agent-universal-layer/openclaw/agy-plugin.json` holds the source, the package and the version, the same way `pin.json` holds OpenClaw's. The source is written down because the npm package of the same name does not exist, and a setup that assumed npm would fail on a new machine.

When the team puts an agent on `google-antigravity-cli/...`, either in `squad.yaml` or already approved in the file in force, `nexgen openclaw setup` does three things in the write step, in this order:

1. It runs `openclaw plugins install clawhub:<package>@<version> --no-enable`. ClawHub is a trusted source, so a fresh install asks nothing and needs no `--force`. When a copy at another release is already there, the command gains `--force`, the flag OpenClaw reads as "overwrite the install target". `--no-enable` keeps activation with the engine, which owns `openclaw.json`.
2. It proves the release on disk: the `version` in `extensions/google-antigravity-cli/package.json` must be the pinned one, or the setup stops with nothing written. ClawHub records the artifact hash of the release it installed, but `plugins install` takes no hash to check against, so the version is the check this engine can make.
3. It writes `plugins.allow` with the backend, `enabled: true`, and `permissionMode: sandbox`, or the guarded mode (`sandbox`, `settings`) the file already declared. `skip` is never written.

A team that names no `agy` model gets none of this: no command, no line, the same file as before. `--dry-run` prints the install command instead of running it, and `nexgen openclaw plan` shows the `plugins` entries the setup would add.

One thing the setup only reports, because it belongs to the person: `agy` on PATH and its login. The credential is not a file the engine reads, since `agy` keeps it in the system keyring (on Windows, the Credential Manager entry `gemini:antigravity`), so the doctor never says "not logged in". It says it cannot see the login, and asks the person to run `agy` once.

The trust `agy` asks for every folder is the step's own work, because it is one absolute path written in a file the engine can see. `agy` records the answer in `~/.gemini/antigravity-cli/settings.json` under `trustedWorkspaces`, and a path covers only itself: the agent workspaces OpenClaw creates are not in that list on a new machine, and an agent that starts in an untrusted folder stops at the prompt. When `agy` is on PATH, the setup appends every agent folder that is missing, in the order the file already has, and leaves every other key alone. It backs the file up first (`settings.json.pre-agy-trust-<stamp>.bak`, beside it) and says how many folders it added and where the backup is. A missing file is created with the one key, `--dry-run` prints the same plan and writes nothing, and a file the engine cannot read as a JSON object is never replaced: the setup leaves it as it is, prints the two lines it used to print, and the person runs `agy` once in each folder and accepts the prompt. A machine with no `agy` command gets no write at all.

`nexgen openclaw doctor` has four lines for this backend and they do not overlap. `openclaw.google.cli_backend` judges the permission mode. `openclaw.google.cli_backend.plugin` says the plugin is missing or is not the pinned release, with the exact install command. `openclaw.google.cli_backend.bridge` says whether the MCP bridge is patched so 'agy' receives connectors lazily through 'lazy-mcp'. `openclaw.google.cli_backend.agy` says the `agy` command is not on PATH or a workspace is not trusted. A missing plugin is never reported as a `permissionMode` problem again.

## Windows, the traps that cost an afternoon

- `openclaw gateway restart` is the only thing that restarts the service: the engine asks it and then waits for the port itself (`nexgen openclaw restart`), because a Windows cold start takes minutes and OpenClaw's own health check is allowed to give up before that.
- Plugins are not changed hot. After a change to `plugins`, restart, and expect the first start to be slow.
- A cold start takes about five minutes. The panel looks dead while it waits; it is not.
- An OpenClaw package from the Microsoft Store installs a second gateway. Two gateways fight over the same port and the same state, and `nexgen openclaw doctor` names the Store package when it finds it. The Windows Hub app described below is a different thing, and the engine recognises it on its own.

## OpenClaw Companion, the Windows app

On Windows, OpenClaw provides the **Windows Hub / OpenClaw Companion** app (`OpenClaw.Tray.WinUI`). It supports three gateway modes:

1. **WSL distribution (`wsl`)**: the app provisions a dedicated WSL2 distribution (`OpenClawGateway`), hosts the gateway inside Linux with state under `.openclaw` inside the distribution, and communicates on `ws://127.0.0.1:18789`.
2. **Local engine gateway (`engine`)**: the companion connects to the host's native Node gateway managed by NeXgen ("a local Gateway on this PC" in the companion settings), sharing loopback and host state.
3. **Isolated native gateway (`isolated`)**: the companion provisions an isolated native MSIX gateway (`OpenClawFoundation.OpenClawGateway`) running inside an isolated Windows session (`isolated-session-v1`) on a high loopback port (for example 52190). It runs under a separate hidden Windows user profile with its own sandboxed workspace and Node runtime. It does not touch WSL, but it cannot read the main user's files or NeXgen state directly.

### What this engine does today

It tells these situations apart from evidence that starts nothing:

| Situation | The evidence it reads | What the engine does |
| --- | --- | --- |
| the native gateway only | the npm package and `~/.openclaw/openclaw.json` | configures it, as always |
| the app installed, naming no gateway | the app's files with no gateway in them, plus the native package or nothing | configures the native one as above, and at the end says the one step that connects the app: Connections, "a local Gateway on this PC" |
| the app on this PC's gateway | a loopback gateway URL on the engine's own port | one line saying the app is connected, and, when the gateway answers, whether its device is in the gateway's list |
| the app's gateway only (WSL or isolated) | the `OpenClawGateway` distribution, or the isolated native gateway session, or a gateway its settings name on this PC | says what it found (WSL or isolated native) and stops before installing or writing anything |
| both | one of those, plus the native package | configures the native one, and notes the app's gateway as a second gateway in conflict |
| the app connected elsewhere | an `sshTunnel`, or a gateway URL that is not loopback | says so; nothing is in conflict |

- The distribution list comes from `HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss`, which answers without starting WSL. The app's files are read as JSON: `settings.json` for the active configuration, `gateways.json` for `url`, `isLocal`, `sshTunnel` and `setupManagedDistroName`, and the isolated session package `setup.json`/`gateway.json` for the native runtime contract. Every field is optional and a file that does not parse says nothing.
- A gateway URL can carry a token in its query string, so it is parsed for its host and port and then dropped: no URL, no token and no device key is ever printed, and a `device-key*` file is never opened.
- The engine never starts the app, WSL or the distribution, and never writes under `%APPDATA%\OpenClawTray`, `%LOCALAPPDATA%\OpenClawTray`, the isolated session profile, or `\\wsl.localhost`.

### How doctor, plan and setup treat each mode

`nexgen openclaw doctor`:

- `openclaw.companion_gateway` reports OK when connected to the engine gateway, or when either the isolated native gateway or the WSL distribution runs alone as the designated gateway. It warns only when an engine gateway and an app-managed gateway run at the same time.
- `openclaw.companion_node` warns when the app's node mode is on beside the CLI node service, or when the app's node has no credential. It takes the place of an OK line above while either is true, and joins a warning line when there is one, because that is the line a person has to act on.
- `openclaw.store_gateway` excludes the companion's isolated native gateway MSIX (`OpenClawGatewayMSIX`), so Store package conflicts are not reported twice.

`nexgen openclaw plan` notes the detected companion gateway mode (`isolated`, `wsl`, `engine`, `remote`) and distinguishes an isolated native gateway from a WSL or remote one in its proposal summaries.

`nexgen openclaw setup`, when an app gateway (WSL or isolated) is active, stops and guides the switch to the native gateway via `--node-gateway` once the app's gateway is stopped. It never modifies, removes or overwrites isolated session files or companion settings.

### The app's gateway and the native one, when both are wanted

The engine configures the native Node gateway, and that is the road it supports: the app is the interface, the gateway is this engine's. When the app runs the only gateway here, the setup stops and says so in the plan and in the run, with the step that fixes it, because a second gateway on the same port helps nobody.

`nexgen openclaw setup --node-gateway` takes that road anyway. Before anything is installed or written it asks whether the gateway port answers, because two gateways cannot share it:

- nothing answers, so the native gateway is installed, configured and started, and the app is told where to look;
- the app's gateway still answers, so the run stops and says to stop it in OpenClaw Companion and try again.

The app's distribution is never started, stopped or written, with or without the option. The app is not something to remove either: the doctor's lines say which gateway is which, never that the app has to go.

### One node per PC: the app, not the CLI service

On Windows the Companion app has its own node mode (`EnableNodeMode` in `%APPDATA%\OpenClawTray\settings.json`), and OpenClaw's CLI has one too: `openclaw node install` registers the scheduled task `OpenClaw Node` and leaves `~/.openclaw/node.cmd` beside it. Both at once are two nodes on one PC. The owner's decision is that the node is the app, so the CLI service is the one that goes.

The order is the part that goes wrong, because the CLI service is the node this PC has today: removing it before the app's node works leaves the machine with no node at all. `nexgen openclaw node --use-app --apply` therefore refuses while the app has node mode on and its node is not connected, and prints the three steps in the order they work:

1. in OpenClaw Companion, open Connections and pair the app with the gateway, leaving node mode on;
2. on the gateway, `openclaw devices list`, then `openclaw devices approve <id>`, and `nexgen openclaw pair --approve-node <node request id>` for the node request;
3. when the app shows its node connected, run `--use-app --apply` again: that is the step that removes the CLI node service and records the role `node`.

The engine reads both facts and writes nothing under either app folder:

- node mode off: nothing new, and the doctor's line about the app's gateway is all there is;
- node mode on and the CLI node service installed: a warning names the two nodes and puts the pairing before the removal, because the removal is the step that can leave this PC with none;
- node mode on and the app's latest node line in its log is `No node credential available`: a warning says the app could not use its node, with the steps that pair it again (`openclaw devices list`, then `openclaw devices approve <id>` on the gateway) and the sentence that the CLI service goes only after that.

The app's log is read from its end, the last megabyte of it, and only when node mode is on: the last `Node status:` line in it is how the engine knows the app's node is connected (`Disconnected` contains `connected`, so the two are matched as whole words). The scheduled task is asked with a read-only `schtasks /Query` that gives up after five seconds. `nexgen openclaw node --use-app` shows that transition and `--apply` carries it out. The engine never writes `settings.json`, never starts the app and never turns node mode off itself: that switch is the person's.

#### The setup code the app can take, and why the engine does not hand it over

The app does accept a setup code without the keyboard. Its local MCP server publishes `app.connection.applySetupCode` (arg `setupCode`, a string, required; it returns `{ outcome, error, gatewayUrl, connected }`) on `http://127.0.0.1:8765/mcp`: POST-only JSON-RPC 2.0, `Authorization: Bearer <token>` with the token in `%APPDATA%\OpenClawTray\mcp-token.txt`, loopback only and closed to browser contexts. The other side of the same handshake is the gateway's `device.pair.setupCode` RPC, which needs `operator.admin` and takes `bootstrapProfile: "node"` for a node. `openclaw devices join-code` is the CLI that calls it, and turns the code into a single-use onboarding URL. `openclaw qr` does not go through that RPC; it mints a mobile setup code locally, from the configuration of the machine it runs on.

That server is not up when the engine needs it. The app's node service starts it as the node connects (`EnableMcpServer` in the settings, `SetMcpEnabled` in the app), and the app's own log shows it listening only in the runs where the node had a credential: with `No node credential available` the node service never attaches and port 8765 refuses the connection. A setup code is exactly the credential that is missing, so the route is a circle whose only way out is the app's Permissions page and its Connections screen. No command-line argument (`--wait-for-pid`, `--post-setup-restart`, `--uninstall`, `--headless`, `--data-dir`, ...) and no `openclaw://` path carries a setup code: the URI scheme takes a gateway address and UI commands, and an unknown path is rejected. Until that changes, the engine mints nothing it cannot deliver and prints nothing it would have to keep, and the transition stays the three steps above.

### What it does not do yet

- It does not configure the app's gateway: no configuration written through `\\wsl.localhost`, no service, no plugin deposited inside the distribution, no pairing inside it.
- It does not install its guardrail plugin there as a package.
- `nexgen openclaw setup` on a machine where the app owns the only gateway stops and names the two ways forward: use the app as it is, or install the native gateway and run the setup again. `--node-gateway` goes on with the native one once the port is free, which means the app's gateway has been stopped in the app.

Full support, which means talking to the gateway through its own protocol and installing the plugin as a package, comes in a later release. Until then the two gateways stay two separate things, and the engine only ever writes the native one.

## Talking to the gateway

The calls in `nexgen_core.openclaw.gateway_rpc` go through OpenClaw's own CLI (`openclaw gateway call <method> --params <json> --json --timeout <ms>`), so the engine speaks to the gateway (`ws://127.0.0.1:18789`) with the same client, the same URL resolution and the same operator scopes as every other OpenClaw command. The module only builds the call and turns the CLI's answer into the engine's own errors.

The token stays where the gateway configured it. A token the caller passes explicitly goes to the CLI as its own `--token` flag and is never printed or logged; otherwise the CLI resolves the configured one and the engine holds none. One primitive per operation:

- `health()`: the gateway's operational state and the plugins it loaded;
- `config_get()`: the configuration snapshot and the base hash the concurrency check needs;
- `config_patch(patch, base_hash)`: a partial configuration update, protected by optimistic locking;
- `plugins_install(source)` and `plugins_reload(id)`: installing a plugin and reloading it in place.

With these calls the engine configures OpenClaw and manages its plugins whatever the hosting environment is (a native Windows gateway, a WSL gateway managed by OpenClaw Companion, a Linux or a remote one): it never has to reach into an isolated filesystem, and it never has to restart a process.

One read-only command inspects the RPC connection with a single call: `nexgen openclaw rpc health [--json] [--url URL]`.

## The plugin as a package, for the gateways the engine cannot reach

The engine deposits the `nexgen` plugin by copying its files into `<state dir>/extensions/nexgen/`, which needs a gateway whose filesystem it can write. The gateway the Companion app runs inside its own WSL distro is not one: `C:` is not mounted there and the distro belongs to the app, so the engine must not reach into it. OpenClaw installs a plugin there from an npm tarball, and `nexgen openclaw package` builds that tarball from the list the deposit itself reads.

It contains the plugin's own files, the shared `nexgen-guardrail-core.mjs` and `vault-briefing.txt`, plus a `package.json` that carries the engine's version, declares `type: module` and points `openclaw.extensions` at `./index.mjs`. The guardrail body, `nexgen-guardrail.config.json` and `nexgen.config.json` stay out: the policy belongs to the Vault, and this artifact is the part that travels. The same files produce the same bytes and the same sha256, so a build that changed unnoticed cannot pass for the one that was reviewed.

The tarball lands in `<state dir>/openclaw/plugin/nexgen-<version>.tgz`. The engine installs nothing itself: moving it to the gateway host and running `openclaw plugins install npm-pack:<path.tgz> --force` there is a separate step someone can see. Without a sidecar beside it the plugin has no policy to run, so it refuses the commands it would judge instead of allowing them: the package carries the mechanism, and the policy arrives by the route that gateway allows.

## The MCP bridge, when the gateway runs in WSL

The Windows companion app keeps OpenClaw in an app-owned WSL distro where interop is off and `C:` is not mounted, so a connector the engine declares as a Windows command (`python ...\lazy-mcp.py`, `node ...\playwright-human-safe.mjs`) never starts there.

`nexgen mcp bridge start` runs those connectors on the Windows side and serves them as MCP Streamable HTTP: one endpoint per connector at `http://127.0.0.1:8765/mcp/<name>`, plus `/mcp` for the gateway itself (`lazy-mcp`). The list of connectors is not written twice: the bridge asks the renderer what this machine mounts directly in OpenClaw and starts exactly that, so a connector added to the manifest appears at the bridge on its own. `--server NAME` narrows it, and `--wsl` binds the virtual WSL interface, the address a distro on the default NAT network reaches (with mirrored networking, loopback is enough).

Every request carries `Authorization: Bearer $NEXGEN_MCP_BRIDGE_TOKEN`. The token is generated into the engine's secrets deposit (`~/.config/nexgen/secrets.env`, 0600) and is never printed; OpenClaw substitutes the variable itself, so no value enters `openclaw.json`. Nothing starts the bridge automatically: `nexgen mcp bridge start`, `stop` and `status` are the whole surface, and `status` exits non-zero when nothing is listening.

`0.0.0.0` is accepted only as an explicit `--host` and only with the token. `stop` asks the listener to finish and waits for the port; it never kills a process by pid, because the pid in a stale state file can belong to somebody else by the time it is used.

## Installation with one command

`nexgen openclaw setup [--yes] [--lan | --server] [--yes-i-know] [--dry-run] [--no-sviluppo] [--node-gateway]` runs nine steps in this order, printing what each one did. It stops at the first step that cannot succeed, and the machine keeps the state it had before that step.

1. **Installation.** If OpenClaw is missing, or is not on the release `openclaw/pin.json` names: `npm install -g openclaw@<pin> --allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw`, with npm resolved to its full path (`npm.cmd` on Windows). A machine without Node gets a clear error and the download link; the engine never installs a system runtime on its own.
2. **Initial configuration.** With no `openclaw.json`, `openclaw onboard --non-interactive --accept-risk --skip-health --no-install-daemon` creates the baseline, and the run continues as if the file had always been there. `--skip-health` because the gateway is not started yet; `--no-install-daemon` because the engine installs the service itself, and two supervisors for one gateway fight over the same port.
3. **Plan and confirmation.** It prints exactly what `nexgen openclaw plan` prints, then asks. `--yes` skips the question; `--dry-run` stops here after printing the plan and the commands it would run.
4. **Safe write**, with the gateway stopped, because OpenClaw does not reload a changed `plugins` block hot:
   - the sections the engine owns, decided by merging the proposal into the file in force: dictionaries merged recursively, a key the person wrote outside the proposal kept, `plugins.allow` merged as a sorted union, model entries merged by `id` so a hand-written model is not dropped, and list-valued leaves such as `deny` and `allowedModels` merged as sets. Only the sections that actually differ go into one patch document, each sent with `--replace-path`, which is what makes a retired key (`tools.exec.security`/`ask`, `models.entries`, the ignored `runtime` block) really leave the file;
   - the background-job models when the default model is local, and `env.vars.CHOKIDAR_USEPOLLING: "1"` on Windows when the gateway would otherwise watch the home, both described above;
   - the gateway token generated with `secrets.token_urlsafe(32)` only when there is none, and never printed;
   - the engine's guardrail plugin enabled only when its folder is actually deposited, and granted `hooks.allowConversationAccess`, which both its governor hook and the evidence check need;
   - `openclaw config patch --dry-run --json` while the gateway is still running: a configuration OpenClaw would refuse is printed with its own reason, and neither the service nor the file is touched;
   - then, with the gateway stopped, `openclaw config patch`, which merges the patch, validates the whole result (schema, SecretRefs and model references), keeps the previous file in its own ring beside it (`openclaw.json.bak`, then `.bak.1` to `.bak.4`), publishes in one atomic step at mode 0600, and refuses without touching anything when it does not like the result. A refusal prints OpenClaw's own error and exits non-zero, and nothing has to be put back;
   - a file OpenClaw cannot parse is not overwritten: the step says so and points at `openclaw doctor --fix`, the release's own repair, then the setup can run again;
   - after OpenClaw has accepted the file, the fallback chains reach the plugin's own `nexgen.config.json`, which is read on every turn and needs no restart.
5. **Agent workspaces.** Each folder is created when missing and the manuals are written only when absent, so a manual the person edited is never overwritten. The one exception is a folder onboarding created a moment earlier: nothing in it can be the person's yet, so the engine's manual replaces OpenClaw's placeholder and its `BOOTSTRAP.md` is removed, because that first-run rite would redefine the agent.
6. **Service.** The gateway is installed as a service when it is not one already, started when the install only registered it, and the port is awaited for up to ten minutes, with the elapsed seconds printed: a cold start takes about five minutes.
7. **Module and bridge.** The host declares `openclaw: local` in `modules.state.yaml`, under its own name, and enables the canonical `openclaw` MCP server (`openclaw mcp serve`) in `manifest.yaml` so other harnesses can reach OpenClaw tools.
8. **Phone.** It prints `openclaw qr`, to be run in a terminal of one's own, and `nexgen openclaw pair` for the approval.
9. **Doctor.** `doctor.collect()` runs and its summary is printed.

After the doctor, on Windows, the run tells the Windows app where the gateway is and offers the request the app leaves. An app installed before the engine names no gateway at all: the run says to open Connections in OpenClaw Companion and choose "a local Gateway on this PC". A request already waiting is printed with its id and what it says it is, then approved only when the person answers yes at that prompt. `--yes` answers the plan, never a device approval, and a run with no terminal approves nothing and prints the command instead. Several requests are listed and none is chosen for the person.

Everything is idempotent: a second `setup` with nothing to change writes no file and restarts no gateway.

### A machine that is always on, with no screen: `--server`

`nexgen openclaw setup --server` is the same nine steps for a Linux machine with no desktop: a VPS, a mini PC in a cupboard. The gateway stays on `gateway.bind: "loopback"` and OpenClaw publishes it with `gateway.tailscale.mode: "serve"`, the pair its own manual recommends (`gateway/tailscale.md`, `gateway/stable-https-url.md`): HTTPS with a real certificate on `https://<host>.<tailnet>.ts.net`, and no port opened on the machine. A `tailscale` section already in `openclaw.json` is kept; only its `mode` changes.

Before anything is installed, four questions decide whether this host may carry a gateway:

1. Is it Linux? A desktop is configured without `--server`; `--server` on another system stops and says so.
2. Is Tailscale installed? If not, the run names the download page and stops: the engine never installs a system program on its own.
3. Is Tailscale logged in? `tailscale status --json` must report `BackendState: "Running"`; any other state stops the run with `tailscale up` as the action.
4. Does the account hold root power? Root itself, `sudo -n true` succeeding, or membership of the `docker` group each stop the run, because a gateway that runs agents must not be able to become root through the process it supervises. `--yes-i-know` is the explicit way past it, and the run prints what it accepted.

`--server` and `--lan` are opposites and are refused together: `--lan` opens the gateway to the network it sits on, `--server` keeps it on loopback and lets Tailscale Serve do the publishing.

The service is the systemd user service OpenClaw installs (`openclaw gateway install`), and the lingering check a Linux host already gets applies here as well: without `loginctl enable-linger <user>` the gateway stops when the last session closes.

## OpenClaw as an MCP server for other harnesses

OpenClaw can serve its tools to the other harnesses (`claude`, `codex`, `antigravity`, `opencode`) via `openclaw mcp serve`. The canonical entry is declared in `03-INFRA/agent-universal-layer/mcp/manifest.yaml` with stdio transport:

```yaml
openclaw:
  exposure: eager
  transport: stdio
  command: openclaw
  args: ["mcp", "serve"]
  targets: [claude, codex, antigravity, opencode]
  enabled: false
```

It is disabled by default and enabled automatically during `nexgen openclaw setup` when OpenClaw is installed on the machine. The OpenClaw renderer explicitly filters it out, so OpenClaw never configures itself as an MCP client.

To go back to a previous configuration, stop the gateway first, copy OpenClaw's own backup over the file, then let it start again so the plugins are reloaded:

```bash
nexgen openclaw stop
cp ~/.openclaw/openclaw.json.bak ~/.openclaw/openclaw.json
nexgen openclaw restart
```

The step keeps five of them, `openclaw.json.bak` being the newest and `.bak.1` to `.bak.4` the ones OpenClaw rotated before it, all at mode 0600.

`nexgen openclaw stop` stops the gateway service through `openclaw gateway stop` and names the processes still alive when it could not. `nexgen openclaw restart [--timeout SECONDS]` asks `openclaw gateway restart` and then waits for the port, printing the seconds it took; a port that never answers is a non-zero exit, never a silent success. A restart that OpenClaw refuses is reported as it came and nothing is started beside it.

`nexgen openclaw pair` lists the phones waiting for approval. `nexgen openclaw pair --approve <id>` approves one, asking first unless `--yes` is given. No token is ever printed: `pending()` strips every field whose name looks like a secret before it reaches the screen.

A node, another machine rather than a phone, gets in through a second request with an id of its own. `nexgen openclaw pair` lists those too, and `--approve-node <id>` shows the node's name, its platform and the commands it would be allowed to run before asking. An id that is not waiting is refused, because an approval that cannot be described is an approval nobody gave.

## Two PCs

One gateway, one node. The gateway is the machine that holds the channels, the cron jobs, the memory and the model routing; the node is a machine that runs OpenClaw's commands for it. What a person reads still arrives through the gateway, wherever the command ran.

A channel lives on one gateway at a time. The same PC can run a gateway of its own and be a node of another gateway, but a Telegram bot cannot answer from both.

### At home: the laptop as a node of the fixed PC

On the fixed PC, once:

1. `nexgen openclaw setup --lan` writes `gateway.bind: "lan"` and a real token. `nexgen openclaw plan --lan` shows the same change without writing it. Check: `nexgen openclaw doctor` says the gateway listens on the home network.
2. `nexgen openclaw restart` if the gateway was already running, so it binds again. Check: the command prints the port answering, and the doctor agrees.

On the laptop, once:

1. `nexgen openclaw setup` installs the pinned release, writes the laptop's own configuration and its own loopback gateway. That local gateway is not the one the node talks to, and this setup does not touch it again. Check: `nexgen openclaw doctor` passes on the laptop.
2. `nexgen openclaw node --host <address of the fixed PC>` prints the two documented routes, in order, with the check after each command. It runs none of them. Check: the printed commands are the ones below. On a PC that already ran a gateway of its own, `nexgen openclaw node --host <address> --apply --join-file <file>` runs steps 3 and 4 and retires the old gateway first; the printed routes stay the reference for doing it by hand.
3. Either the one-paste route: `openclaw devices join-code` on the fixed PC, then the command it prints, `openclaw connect <url> --service --session-host`, on the laptop. OpenClaw approves the device and its first command surface in the same step; `--session-host` also lets the node run agent sessions, so keep it to a machine you trust as shared infrastructure. The URL is single use, lasts about ten minutes and carries administrator access: treat it like a password and paste it nowhere else. Or the explicit route: `openclaw node install --host <address> --port 18789` and `openclaw node start` on the laptop, then `nexgen openclaw pair` on the fixed PC to read the device request, `nexgen openclaw pair --approve <device id>` to admit it, a reconnect that asks for the commands, and `nexgen openclaw pair --approve-node <node request id>` to allow them. Check: `openclaw node status --json` on the laptop.
4. On Linux, `loginctl enable-linger <user>` once: without it the systemd user service stops when the last session closes. `nexgen doctor` already says when lingering is off.

On the fixed PC, to see the result: `nexgen openclaw doctor --nodes` adds the paired nodes to the report and checks that the node accepts the commands an agent asks it to run, and `nexgen openclaw pair` lists them with their state. Neither approves anything on its own.

### What the doctor says on a node

On a machine that is only a node, `nexgen doctor` and `nexgen openclaw doctor` stop judging the gateway: bind, token, exposure, the port it listens on, the plugin, the agents and their models are not read there, and one line says why. The `openclaw.json` the PC kept from when it was a gateway stays where it is, because the node still reads `nodeHost` from it, and it is not a fault.

Three facts make the machine a node, in this order: the role it recorded (`nexgen role set node`, also written by `install.ps1 -Role node` and by `nexgen openclaw node --apply`); the node service OpenClaw installed, pointing at a gateway elsewhere, for a PC migrated before that role existed; the Companion app with its node switch on and a gateway elsewhere in `gateways.json`. The app's files are read and never written. A machine that still runs a local gateway service is a gateway, and keeps the full report.

### A gateway behind Tailscale Serve, and a laptop on the same tailnet

On the always-on machine: `nexgen openclaw setup --server`, then `nexgen openclaw restart` if the gateway was already running. Check: `nexgen openclaw doctor` says the gateway listens on loopback and Tailscale Serve publishes it over HTTPS on the tailnet.

On the laptop: `nexgen openclaw node --host <host>.<tailnet>.ts.net` prints the same two routes and recognises the MagicDNS name. The join code carries the HTTPS address the gateway mints, so `openclaw connect <url> --service --session-host` needs no port and no token of its own; the explicit route is printed as `--port 443 --tls`, because that port belongs to Serve. Both machines have to be on the same tailnet and logged in to Tailscale. Check: `openclaw node status --json` on the laptop, and `nexgen openclaw pair` on the gateway for the approval.

### Outside the house

The laptop keeps its own gateway in loopback, with no channels. That is what a person reads from when the fixed PC is out of reach, and it is why the doctor's line about a loopback gateway is information rather than a fault there: a node on another PC cannot reach it, and on that machine nothing should.

The node connection to the fixed PC exists only while the two machines can reach each other. Approvals are not lost when it is down, and nothing else is either: memory, channels and cron jobs never lived on the laptop.

### What stays the person's

- The address of the other machine. The engine does not guess it and does not read it from anywhere.
- Both approvals, the device and the commands. Nothing is approved automatically, and every request says which machine asks and what it would be allowed to run.
- Which gateway owns a channel. Putting one bot on both gateways is the mistake this page exists to prevent.

## Commands

`nexgen openclaw plan [--json] [--lan | --server] [--acpx]` reads the configuration in force, computes what the engine would write, and prints what it would add, what it would change, what it no longer proposes, and which keys are the person's and stay. It writes nothing; `--server` is the read-only preview of the change `setup --server` applies.

`nexgen openclaw doctor [--json] [--nodes]` checks the installation, the version against the pin, the configuration's validity, `:latest` ids, caged local models, whether the evidence check runs and for which local models, the heartbeat, the gateway's bind and authentication, how the gateway is exposed (loopback, the home network, every interface, a direct tailnet listener, Tailscale Serve or Funnel, with a failure when a gateway published through Serve also listens on a network address), Google entries, the local `agy` backend and the permission mode its plugin was given, ACP seats, agents on subscriptions this machine does not have, which background job would wake a local model, whether the gateway watches the whole home recursively, whether the gateway is listening, whether every agent has a fallback chain, a second gateway from the Microsoft Store, the OpenClaw Companion's own gateway (reported, never configured), and how many skills and servers OpenClaw created that the canonical library does not declare yet.

When the app is installed, one line answers the two questions a person has: whether the app's settings point at this PC's gateway, and whether the gateway's own device list knows the app. `nexgen openclaw doctor` reads that list with `openclaw devices list --json`, on that line only and only when the app says it is on this gateway; a stopped gateway is a normal state, so the line then says nothing about devices instead of reporting a fault.

`--nodes` adds the nodes the running gateway knows, which costs one run of OpenClaw's CLI and is therefore never done unless asked; the line about the network needs no query and is always there. The same flag reads each node's own exec approvals for every agent that runs its commands there, and warns when that PC is stricter than what the agent asks for: a node whose fallback is `deny` answers the agent with `exec denied: request-failed`, which says nothing about the cause. Without the flag nothing about a node is asked. `nexgen doctor` runs the same checks as part of its own report, without `--nodes` and without launching that list, and says nothing at all on a machine that never installed OpenClaw, never declared the module and has no Companion gateway.

`nexgen openclaw node --host <address> [--port 18789] [--name <label>] [--apply] [--join-file <path>] [--dry-run] [--force]` prints the commands that make this machine a node of a gateway elsewhere: OpenClaw's one-paste route with a join code, and the explicit route with its two approvals. The address may be a name on the home network or a Tailscale MagicDNS name; the second needs no port in the one-paste route and `--port 443 --tls` in the explicit one. Without `--apply` it writes nothing and runs nothing, because `openclaw node install` already writes the service on each platform.

`--apply` does the machine's side of that transition, in the only order that works on a PC that already ran a gateway of its own. It stops and removes the local gateway service, archives a systemd drop-in left without its unit, drops the gateway's `auth` block, switches local channels off, and turns `nodeHost.workerRuns` on. All three are leftovers that break a node in a way no doctor line names: the token in the file wins over the pairing token and the central gateway answers `AUTH_TOKEN_MISMATCH`, two gateways polling one bot kill each other's poller every thirty seconds, and a node without worker runs hosts no sessions. Every other key of the configuration stays, and the file is copied aside before the single write. Then it enrols: with `--join-file` it hands the link to `openclaw connect --service --target-file`, which pairs the machine, confirms the first authenticated connection and installs the service before it returns, so a successful exit is the whole proof and there is nothing left to wait for; without one it runs `openclaw node install` and `openclaw node start` and leaves the two approvals to `nexgen openclaw pair` on the gateway. `--dry-run` prints the same plan and changes nothing, `--force` moves a machine that is already a node of another gateway. `install.sh --role node` and `install.ps1 -Role node` run this same retirement before attaching.

`nexgen openclaw node --use-app [--apply]` is the other direction, for the Windows PC whose node is the Companion app: it reads whether the CLI node service `OpenClaw Node` is installed and whether the app's node is connected, and with `--apply` removes the service with `openclaw node uninstall` and records the role `node`, but only once the app's node is connected. While the app has node mode on and its node is not up, `--apply` refuses and prints the three steps in the only order that works. It never writes under the app's folders and never starts the app; the section on the Companion app says what the app itself still needs.

`nexgen openclaw setup` applies that same plan: it installs the pinned release, onboards when there is no file, merges the owned sections with a backup beside them, writes the agent manuals, installs and starts the service, declares the module and runs the doctor. What the plan promises is what the setup writes, because both come from the same pure function. On a Windows machine it also ends by pointing the app at this gateway and offering the request the app leaves, in the paragraph that follows the summary the doctor prints.

`nexgen openclaw adopt [--skills] [--mcp] [--dry-run] [--yes] [--auto] [--json]` takes what was created inside OpenClaw and makes it canonical: the skills the Skill Workshop learned, the servers in `mcp.servers` that no manifest declares, and ClawHub plugins wrapping MCP servers, detected from plugin manifests `openclaw.plugin.json`, `package.json`, bundled `mcp.json`/`.mcp.json`, or catalog descriptions matching official or hosted MCP services. The underlying MCP servers are extracted and proposed as unmanaged servers, with credentials in `${VAR}` preserved as references. Installed plugins that are not MCP wrappers (channels, memory, model providers) are left out with a one-line note.

It prints the plan with every check before writing anything. A name already taken is a refusal and never an overwrite, a plaintext credential blocks, and a skill holding a script, an executable or an unknown file is a question: `--yes` adopts only what the checks cleared and names what it left. An adopted skill is copied into `03-INFRA/agent-universal-layer/skills/<name>/`, declared `origin: vault` with `openclaw_id` in the skills manifest, built into every view and archived in the Workshop through `openclaw skills workshop archive`, so OpenClaw loads the canonical copy like every other CLI. An adopted server enters the MCP manifest lazily, through the same writer `nexgen mcp add` uses, stamped with `origin: openclaw` and its OpenClaw identifier.

Canonical resources without a provenance mark are never removed by adoption. The mark differs per manifest because `origin` already means something else in the skills one: there it names where the bytes are materialized from (`engine`, `vault`, `github`, `installer`, `upstream`), and an adopted skill is a vault skill, since the copy lives in the Vault like any other. Its OpenClaw provenance is the `openclaw_id` it carries, and the skill's own `SKILL.md` frontmatter records the same pair. The MCP manifest had no such key, so `origin: openclaw` is the mark there. Either way, an entry without a mark belongs to the library and this mechanism never touches it.

With `--auto`, adoption runs non-interactively: resources verified as SAFE enter the canonical library immediately, while everything else, whether it needs a person or was refused by the checks, stays outside and is recorded in machine-local state (`openclaw-adopt-state.json`, under the engine's state directory, never the Vault and never the repository) with the reason, for the plugin to show. `--auto` also tracks absences for MCP servers: a server adopted from OpenClaw and absent from it for two consecutive passes is retired from the manifest, listed under `retired_servers`, and unmounted by the next sync. A single missed pass, a broken configuration or an unreachable gateway removes nothing and says so, and a retirement whose write fails is reported as a failure, never as an exit.

Skills are admitted by the same pass and are never retired by absence, because absence there means nothing: an adopted skill was copied into the Vault and its Workshop original archived, which is exactly what adoption does, so a poll of the Workshop would find it gone and delete the copy the library had just taken in. A skill leaves the library when its manifest entry is removed, which is a person's edit and not a side effect of a timer. A server is different: `openclaw.json` is the person's file, its `mcp.servers` table is the honest record of what OpenClaw has, and the engine never writes that table. A name already listed under `retired_servers:` is not adopted back; remove it from that list first, the same rule `nexgen mcp add` states, so bringing a server back stays a decided act.

The OpenClaw `nexgen` plugin registers `/adopt` to run a pass from chat and to show what is waiting, and the `doctor` prints one line for the last pass: what entered, what left, and what waits for a person.

The pass runs from three places, all of them in the plugin OpenClaw already loads: `/adopt` in chat, the `skill_changed` hook (the only lifecycle event OpenClaw announces for what the Workshop and ClawHub create), and the gateway's own clock, one bounded pass every 30 minutes while the gateway is up, the same cadence as the engine's guard cycle, armed on `gateway_start` and cleared on `gateway_stop`, one pass at a time. That is what covers a removed MCP server: OpenClaw's hook catalog (`docs/plugins/hooks/reference.md` in the installed release) has no equivalent for `mcp.servers`, so the tick is the pass that notices. A tick that could not start the engine writes the sentence to the gateway log; a skill change stays quiet, so the Workshop's own notice reads alone.

`nexgen openclaw package [--json]` builds the plugin's npm tarball in the engine's state folder and prints the file and its sha256, the pair an install on another gateway is checked against. It installs nothing and writes nothing else; `--json` reports the same path and hash with the files inside.

`nexgen openclaw cost [--json] [--since DATE]` prints what the bot spent, per day, per session and per agent, through the optional [ccusage](https://github.com/ccusage/ccusage) binary (MIT): the daily and session views of OpenClaw's own transcripts, cached for ten minutes, with each session attributed to the agent folder that owns its file and counted whole on the day it was last active. It writes nothing, prints one line instead of a number when the binary is missing or its output shape is not understood, and `--json` reports the same reading as numbers.

`nexgen openclaw parity [--lane LANE] [--json] [--dry-run]` measures what a harness costs inside OpenClaw against what it costs run directly: one agent per CLI-backend lane, the same short message on both sides, the total input of the one model call, and the difference judged against the owner's 10,000-token tolerance. It keeps `99-INDEX/OPENCLAW-PARITY.json` in the Vault, or in the state directory with the path printed when this machine may not publish there, and `--dry-run` prints the commands and spends nothing. The section above says what it measures and which lanes it does not.

`nexgen openclaw restart` and `nexgen openclaw pair` bring the gateway back and let a phone, or another machine, in; they are described above, with the rest of the installation. `nexgen openclaw stop` takes it down and waits until the port is free, which is what a hand edit of `openclaw.json` needs.

`nexgen openclaw rpc health [--json] [--url URL]` inspects the gateway's operational state through OpenClaw's own `gateway call health`, reporting uptime and the plugins it loaded without changing anything.

`nexgen openclaw groom [--dry-run]` compacts the agent chats that sat idle or grew too large, capped at at most one session per run (the largest eligible one, prioritizing sessions over the hard threshold; overridable via `NEXGEN_OPENCLAW_MAX_COMPACTIONS`, and `0` switches the groom off) to keep the single-threaded gateway responsive. An idle chat is compacted only above `IDLE_MIN_TOKENS` tokens (150,000, the measured first-prompt budget): summarising a smaller one costs more than the cold cache it prevents, and the floor is what keeps an hourly bill off a bot nobody is talking to. A chat whose compaction failed does not take the run from the next one and is left alone for six hours, so one chat the gateway refuses cannot starve the queue, and a run that leaves chats waiting while compacting none of them raises one alert through the engine's alert channel. `--dry-run` previews the whole queue and says how many a real run would take. `nexgen openclaw doctor` judges the hourly job itself (`openclaw.cron.context_guard`): a job that is missing, switched off, or registered with a delivery mode instead of `--no-deliver` is a warning, because the second one makes the gateway record every run as a failed delivery.

Which chats it can compact is a fact about their lane, not about their size.
A `:harness:` chat belongs to a native harness (codex, claude-cli, copilot) that declares its own compaction, so `openclaw sessions compact` asks the harness and the chat follows the idle rule like any other.
A `:acp:` chat is bound to an external agent through the Agent Client Protocol, whose request surface has no compact request at all: OpenClaw can only rewrite its own transcript mirror while the agent resumes its own conversation, so the next message would re-send everything.
Those chats are left alone and named instead, by one line the run prints and by `openclaw.groom` in the doctor, which lists each one over `IDLE_MIN_TOKENS` as not compacted together with any chat a harness refused and the memory then stopped retrying (codex answers `compacted: false` while its agent carries a tools deny list).
That doctor line is red when the hourly job is missing, switched off, has never finished a run, ended its last one with an error, or has not succeeded for three hours, and green with the age of the last run and the number of chats waiting.
The run also puts back the Antigravity plugin's manual compaction when a plugin update removed it, because `agent-sync` only aligns the OpenClaw release, and without it every `agy` chat was silently skipped until a person ran `nexgen openclaw setup` again.
