# The quota: what is left of each plan

🇮🇹 [Italiano](it/quota.md)

No provider offers a local answer to "how much of this plan is left", and the engine does not invent one.
It reads what the harnesses on this machine have already written about their own usage, repeats the numbers they give, and says plainly which windows cannot be measured from here.

One optional exception exists, and you install it yourself: [tokscale](https://github.com/junhoyeo/tokscale) is a separate MIT binary that reads the logins this machine already stores and asks each provider's own quota endpoint.
When it is on `PATH`, `nexgen quota` shows its live percentages beside the local ones, and a plan it answers for is shown once, from that number.
The engine itself still reads no credential file and calls no provider endpoint: see [the optional backend](#the-optional-tokscale-backend).

`nexgen quota` prints one row per provider and window.
`/quota` prints the same rows inside OpenClaw.
`nexgen usage` prints the same readings grouped by provider, and `/spend` shows them inside OpenClaw.

## The surfaces

| Surface | What it gives |
| --- | --- |
| `nexgen quota` | one line per row: provider, window, what was used, the percentage when it is a real one, the reset when the provider gave it, and how old the reading is |
| `nexgen quota --json` | the same rows as data, plus `lines`: the text already rendered, in the machine's language |
| `nexgen quota --line` | one compact line: each plan's worst window, its reset and, past ten minutes, its age. What a session start injects |
| `/quota` in OpenClaw | the `lines` of the JSON, forwarded to the chat |
| `nexgen usage` / `nexgen usage --json` | the same readings, one block per provider: the spend of the flat lanes, the tokens Claude Code used, the percentage Codex and Antigravity report, and a sentence where a lane cannot be read |
| `/spend` in OpenClaw | the `lines` of `nexgen usage`, forwarded to the chat |

The engine formats the strings once.
The plugin never turns a number into a sentence and never rounds a percentage, so the chat and the terminal cannot disagree about the same reading.
The command always exits 0: a source that cannot be read is an answer, not a failure.

## The line a session starts with

Claude Code's `SessionStart` hook injects one compact line, so the first turn of a session already sees what is left instead of the person having to ask:

```
Quota: Claude 5h 40% (reset 18:00) · Codex 30d 72% · OpenCode Go weekly 0% · Antigravity stale
```

It is `nexgen quota --line`: the worst window of each plan, the reset the provider gave, and `stale` for a plan whose reading could not be refreshed.
A plan with no percentage at all — spend without a declared ceiling, or something this machine never measures — is left out rather than guessed at, and the full report is where it appears.
The hook formats nothing; it prints the engine's own sentence, so the session context and the terminal cannot disagree either.
The command writes that sentence as UTF-8 whatever code page the console started with, because the hook spawns it with a pipe and decodes it as UTF-8: the `·` between two plans is a character, not a single byte of a Windows code page.

The hook asks for that line with a hard three-second cap and prints nothing at all when the call fails or runs out of time: a session start must never depend on a reading.
For the same reason the line never buys a run of [the optional backend](#the-optional-tokscale-backend): it shows that backend's last answer while the `failover.state_max_age_minutes` of `quota.yaml` still trusts it, and past the ten minutes every caller shares it says how old the number is (`read 40m ago`).
A plan the machine has nothing for is left out rather than guessed at.
The hook's own job is unchanged: the vault briefing still arrives on a resumed or compacted session, and a machine with no engine on `PATH` simply gets no line.

## Why the chat command is `/spend` and not `/usage`

OpenClaw has its own `/usage`: the per-response footer, and `/usage cost`, a local summary summed from OpenClaw's session logs.
That sum reports `$0` for an agent on a subscription and knows nothing about OpenCode Go spend or a Claude plan window, because neither is in a session log.
The engine's readings answer that question, so they travel beside OpenClaw's command instead of taking its name.
A plugin command is matched before the built-in ones, so registering `/usage` here would remove the footer and the cost summary from the chat.

## What each provider gives

Every plan is read from the best source that can answer for it, and only from
that one: a live reading from the optional backend first, then this engine's own
reader, then `not measured`.
The order is the same for every provider and is never mixed, so no plan is
printed twice and no row is a guess.

| Provider | Source used | What is shown | Known gaps |
| --- | --- | --- | --- |
| Claude Code / Claude Max | tokscale's live session and weekly windows; the `message.usage` counters of `~/.claude/projects/*/*.jsonl` when it has nothing | `Session` and `Weekly` percentages with the reset and the age of the reading; the tokens of the last `5h`/`7d` from the transcripts otherwise | the transcripts hold no plan ceiling, and Anthropic throttles the usage endpoint: while it does, the last good reading is shown without a percentage |
| ChatGPT / Codex | tokscale's live session, weekly and monthly windows; the last `rate_limits` event of the newest `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` otherwise | `5h`, `7d`, `30d` as the provider declares them, with `resets_at` | the plan name is whatever the logged-in account reports, so a second account on the same machine is a different plan |
| OpenCode Go, OpenCode Zen | tokscale's rolling, weekly and monthly windows; the harness archive `~/.local/share/opencode/opencode.db` otherwise | the provider's own three percentages; the archived spend per window, with a percentage only when `quota.yaml` declares a ceiling | the archive reports dollars, never a plan ceiling |
| Google Antigravity | the IDE's own loopback endpoint, through tokscale; the local `*-Antigravity Quota.log` written by its quota extension when that endpoint cannot be reached | `30d` prompt credits, with the age; the log reading is dropped as stale after a day | needs a running language server and, from version 1.5.3, its CSRF token: with neither, the local log is the only source, and a machine that only runs the `agy` CLI writes nothing to disk |
| GitHub Copilot | only tokscale's live reading | `Chat`, `Completions`, `Premium`, with the reset the provider gives | no local file this engine can read |
| anything else tokscale learns | only tokscale's live reading | whatever the provider names, at the provider's own percentage | no local reader, and none is invented |

### Codex

Codex writes a token-count event into its session file, and that event carries the plan's own `rate_limits` block: the percentage used, the length of the window in minutes, and when it resets.
The engine reads the tail of the newest session, at most 8 MiB, and repeats the numbers without recomputing them.

A reading older than the window it describes is printed as stale and its percentage is dropped.
After a window has rolled over, the old percentage describes a period that has already reset, and repeating it would be worse than saying nothing.

### OpenCode

The spend is what the harness archived, split by the provider recorded on each session: `opencode-go` (the flat subscription) and `opencode` (Zen, pay per use).
The dollars per session come from the optional [ccusage](https://github.com/ccusage/ccusage) binary (MIT) when it is installed, which is what prices a flat subscription's usage instead of reading the zero it stores, and the archive's own `cost` column when it is not.
A model id ending in `-free` costs zero, so a free lane appears as `0.00 $` instead of disappearing: a plan with no cost is a fact worth one line.

The arithmetic is the harness lane's own, in `nexgen_local/supervision_spend.py`.
The quota report decides what a person reads, never how a cost is computed.
An engine installed without that lane says so in one line and shows the other sources.

### Claude Code

`nexgen quota` sums the `message.usage` counters of the transcripts written in the last seven days, split into a 5h and a 7d window, deduplicated by message id.

It prints token counts and no percentage, because Claude Code's plan usage is not in any local file (`~/.claude` settings, cache or transcripts): `/usage` queries Anthropic's remote plan-usage endpoint dynamically.
The line says so: `the limit is not readable on this machine`.
That is the honest answer, and the engine does not turn it into a guess.

This command is the only reader of those transcripts.
They were 613 MB in eight days on the machine this was written on, which is affordable for a command someone typed and not for an hourly timer, so the beat and the doctor use the cheap sources instead.

### Antigravity

The first source is Antigravity's own CLI. `agy -p /usage` prints the plan's windows the same way its interactive `/usage` does (Gemini and Claude/GPT models, each with a weekly and a five-hour limit, the share remaining and the reset), and the engine turns each row into a quota row: used is what the client says is gone, and the Gemini rows carry the provider id `google-antigravity-cli`, so the delegation gate reads them like any other plan. The command answers from Google's quota service with the login the client already holds; the engine never reads that login or calls Google itself, and it costs no model tokens. One answer is kept for ten minutes in the state directory, so the hourly beat, the doctor and a session's first line share it; `NEXGEN_QUOTA_AGY=off` stops the client from being asked. Only where `agy` is not installed, or prints nothing readable, does the engine fall back to the IDE extension's log below.

Antigravity's desktop environment logs prompt credit usage through the `ag-quota` extension into local session logs (`*-Antigravity Quota.log` under `%APPDATA%\Antigravity IDE\logs` or `%APPDATA%\Antigravity\logs`).
The engine reads the tail of the newest log, extracts the monthly prompt credits used percentage for the `30d` window, and carries the moment the log entry was written, so the row prints how old the reading is like every other row.
The reading is dropped as stale after a day, not after the 30 days of its window: the extension only writes while the IDE runs, so a percentage that is still inside its monthly window can be weeks old and describe a period that has already reset.
A stale reading keeps its age, loses its percentage, and never reaches the state the plugin reads before a turn, so an old 99% cannot make the engine block a plan that is fine.
When no local log exists on disk, such as on a machine that only uses the `agy` CLI, where quota is fetched in memory from the Google Cloud Code API and never written, the row reports `not measured`.

The live source is a different route to the same number, and it is the backend's, not this engine's: tokscale asks the language server the IDE runs over loopback (`RetrieveUserQuotaSummary`), and it finds that server through the `--csrf_token` the process was started with, the port the CLI writes into `cli.log`, or an `ANTIGRAVITY_CSRF_TOKEN` in the environment.
Since language server 1.5.3 the token is required, so a server started without one is not asked at all, and a machine whose `cli.log` names no port has nothing to probe.
That is the whole fallback: the live endpoint, then the local log, then `not measured`, never both, and never a second row for the same plan.

## The optional tokscale backend

[tokscale](https://github.com/junhoyeo/tokscale) is an MIT-licensed binary (npm `tokscale`) that does what this engine deliberately does not: it reads the OAuth logins already stored on the machine and asks each provider's own quota endpoint how much of the plan is left.
Install it with `npm install -g tokscale` and it is used; do nothing and the report is exactly what it was before.

It earns its place with the rows no local file can answer:

- a Claude Code plan, which reports tokens in its transcripts and no ceiling anywhere on disk;
- Antigravity, whose IDE answers on its own loopback endpoint but writes nothing to disk on a machine that only runs the `agy` CLI;
- OpenCode Go, rolling, weekly and monthly, as the provider computes them instead of as a declared dollar ceiling;
- Copilot, and any other plan tokscale learns to read, without this engine growing a reader for each one.

### What it does not change

The local readers stay, and they are the fallback.
When tokscale answers for a plan the engine also reads locally, the live number is what you see and the local row is hidden, so nothing is printed twice.
That includes Claude Code's token counts, which are not printed beside a live reading of the same plan: one plan, one row, one meaning.
When tokscale has nothing for that plan, the local row is printed as it always was, including the Codex percentage from its own rollout, the harness archive's dollars, and Antigravity's log.
`nexgen usage` still shows the harness archive's spend per provider, so the dollars of OpenCode Go are not lost when a quota percentage replaces them in `nexgen quota`.

### Its ten minutes of memory

One run costs a few seconds and asks the network, and `nexgen quota` is not the only caller: the hourly beat that raises the two alerts, `nexgen doctor` and the supervisor's pre-dispatch check all read the same percentages, while the line a session prints when it starts reads their last answer without buying one of its own.
The answer is therefore cached for ten minutes under the machine-local state directory (`~/.local/state/nexgen/tokscale-usage.json`, `%USERPROFILE%\.local\state\...` on Windows), one interval in one constant, so the engine never spends more than one run of the binary per ten minutes however many of those callers ask.
The run itself starts in that same directory: tokscale keeps a `.tokscale/` folder in the working directory it is given, and left to inherit the caller's, it would write that folder into whichever repository, Vault or shell the command happened to be run from.
Each row says how old its number is.
The cache holds only what this report reads: provider, provider id, window, percentage, reset, and the moment the number was read. The login, the e-mail and the account id that the binary also prints are never written anywhere.

### When a provider throttles

Anthropic's usage endpoint is rate-limited, and asking it too often is how a machine ends up being told to wait half an hour.
tokscale records that wait itself, for each login it holds, and the engine reads that record instead of inventing a second schedule: while the wait lasts, no further run is spent, and the rows come from the cache with the age each of them carries.

What the report then says, and why:

- A reading that tokscale could not refresh is dated by the moment it was really taken, not by the moment of this run, and it loses its percentage: `Claude: 34% of the Session window in the last reading (read 40m ago), and it was not refreshed`. The number is real, its age is real, and no handover acts on it.
- The rows of the other plans keep their percentages while the wait lasts, and their own age is printed on each of them. A cached answer is reused no longer than the age the policy itself trusts (`failover.state_max_age_minutes`, three hours unless the owner declares otherwise), so nothing reaches a turn with an age that policy would have refused; past that age the answer is bought again and comes back as the kept reading it is, without a percentage.
- One line at the bottom says why, and there is one sentence per age: several plans whose last samples were taken far apart are each described with their own. When the last reading was kept: `tokscale could not refresh Claude, and is rate-limited until 22:41 (10m from now): showing the reading from 40m ago for it.` When nothing could be refreshed at all: `tokscale is rate-limited until 22:41 (10m from now): these readings are from 15m ago.` When tokscale has no sample left and Claude is missing from the answer altogether: `Claude: tokscale is rate-limited until 22:41 (25m from now), so the plan's own windows are not shown.`

The retry time is not in `tokscale usage --json`: the binary prints that sentence on stderr and only in its text view, so a JSON answer from a throttled provider carries the last sample with `freshness.stale` set and no deadline at all.
The record tokscale leaves beside it is the machine-readable copy of the same deadline, and it agrees with that sentence to the second.

A provider that stops being answered at all keeps the last reading it gave for a day, marked the same way; after that it leaves the report instead of lingering as a number nobody can date.

### When Claude's token expires

tokscale asks Anthropic with the access token Claude Code stored, and once that token is rejected it omits the plan from an otherwise good answer, so Claude's windows disappear from the report with nothing on it to say why.
When Claude is missing and `claude` is on `PATH`, the engine runs `claude auth status` once and asks tokscale once more: that command makes Claude Code rewrite its own login, spends no model call and no window of any plan, and its output is discarded unread because it names the account's e-mail, so nothing of it is cached, logged or shown.
The refresh happens at most once per cache period, so a machine whose login cannot be renewed costs one attempt every ten minutes rather than one per caller, and it is never attempted while tokscale has recorded a cooldown, because a throttled plan is missing for another reason.
When it fails the report is what it was before, Claude unknown, plus one sentence saying the token was rejected and its login could not be refreshed.

### When it goes wrong

A missing binary, a non-zero exit, a run that passes twenty seconds, or an answer whose shape this engine does not recognise costs one line at the bottom of the report and nothing else.
When an earlier run had already answered, its numbers stay on the report beside that line, dated by the moment they were really read and marked as kept, so one timeout does not blank every plan for the ten minutes the failure is remembered.
The command still exits 0, the local readings still print, and the doctor and the beat carry on.
The output carries no schema version, so a shape that is not understood is refused whole instead of half-printed: a tokscale release that renames a field is a line that says `tokscale output format not understood`, never two numbers and a guess.

### Turning it off

```yaml
# 03-INFRA/agent-universal-layer/quota.yaml
backends:
  tokscale: off
```

The switch is per machine and lives beside the ceilings it belongs with. `NEXGEN_QUOTA_TOKSCALE=off` says the same thing for a single run, without editing the file: a scheduled job, a script, or a sandbox that must not read the accounts of whoever is logged in.

## A percentage needs a ceiling

OpenCode reports what was spent, never how much was allowed.
A percentage appears only when the machine declares the ceiling, in `03-INFRA/agent-universal-layer/quota.yaml` inside the Vault:

```yaml
limits:
  opencode-go:
    5h: 12
    7d: 40
  opencode:
    30d: 20
```

`templates/quota.yaml.example` is the same file with the comments that explain it.
The ceilings are per provider and per window, in dollars, and only for the windows the report shows.
An entry that is not a positive number is ignored and named in one line at the bottom: a declared limit that cannot be used must not become a silent zero.

Without the file every paid lane shows its spend and no percentage.
No ceiling is ever inferred from a previous month, from a plan name or from a guess.

## The two alerts

The percentages raise an alert at 80% and again at 95%.
The thresholds live in one place, `quota.ALERT_AT`, and the alert and the doctor read the same tuple, so they cannot disagree about what "high" means.

The alert is raised by the beat that already runs every hour, on both platforms, right after the dependency watch.
It goes through the megaphone, like every other alert, and carries the provider, the window and the reset when the provider gave one.

The debounce key contains the band: `quota:<provider>:<window>:80` and `quota:<provider>:<window>:95`.
Keying on the provider alone would let the 80% alert silence the 95% one, which is the alert that matters most.
The megaphone's own four-hour debounce applies on top: one missed repeat is cheaper than an alert every hour on a plan that is simply full.

Only the cheap readings are consulted.
No timer opens the Claude Code transcripts.
The optional tokscale backend is asked from its ten-minute cache, so an hourly beat pays for at most one run of it per ten minutes.

## What a plan hands over to

The `failover:` section of the same `quota.yaml` declares what a turn should use when a plan runs out, and in which order.
It is empty by default: which providers to pay for, and in which order, is your rule and not the engine's.

```yaml
failover:
  block_at: 95
  state_max_age_minutes: 180
  chain:
    - anthropic/claude-opus-5-5
    - opencode-go/deepseek-v4-pro
  agents:
    capo:
      - openai/gpt-6-luna
```

`chain` applies to every OpenClaw agent the engine creates, `agents` overrides it for one of them, and every entry is a model written the way OpenClaw names it, `provider/model`.
A malformed entry is ignored and named in one line at the bottom, like a declared limit that cannot be used.

With that section in place, each row of `nexgen quota` ends with the model that window hands over to:

```
Codex (free): 96% of the 30d window used (read 2m ago), resets in 3h 12m, then anthropic/claude-opus-5-5
```

The model shown is the first entry of the assistant's chain that runs on a different provider, because a spent plan takes its own sibling models with it.
A provider with no chain declared prints its row as it always did, and no handover is invented.

`nexgen openclaw setup` carries the chains to OpenClaw: see [the OpenClaw page](openclaw.md#the-fallback-chain).
`nexgen doctor` prints one line, `openclaw.fallbacks`, with how many agents carry a chain and which ones carry none.

## Subscriptions only (optional)

Nothing on this page is needed by a machine that pays per token, and no part of the engine asks you to switch. This section is for one case: every plan you run is flat, a subscription or a model on this machine, and you want the engine to stop offering the rest.

The key is one line in the same `quota.yaml` that holds the ceilings:

```yaml
# 03-INFRA/agent-universal-layer/quota.yaml
flat_providers: [opencode-go, anthropic, openai, ollama]
```

Each entry is a provider id, written the way a model ref names it before the slash. With the key declared:

- `nexgen openclaw setup` writes `agents.defaults.modelPolicy.allow` with one wildcard per declared provider and nothing else, so every agent's picker offers `opencode-go/*`, `anthropic/*`, `openai/*` and `ollama/*` and no metered plan. Without the key the same setup writes one wildcard per reachable provider, as it always did.
- `nexgen openclaw plan` shows the allowlist that setup would write before any file is touched.
- `nexgen doctor` and `nexgen openclaw doctor` print one line, `openclaw.flat_models`. A model ref outside the list is a `FAIL` that names where it was read and which model it is: an agent's `model`, a link of its fallback chain, or a `subagents.model`. Both commands exit non-zero on it, because a turn on a metered provider spends money nobody approved.

The list is a policy, not a rewrite: the setup changes which models the pickers offer and touches no agent's `model`, so a file that already names a provider outside the list keeps it, is reported, and stays yours to change.

A provider belongs in the list only if that is how this machine reaches it. The same provider is often both a plan and a meter: `anthropic` is a Claude subscription through Claude Code and also a paid API key, `openai` is a ChatGPT plan through Codex and also per-token usage, and OpenCode's `opencode` (Zen) is metered while `opencode-go` is the flat one. The list names the provider, not the account, so an entry there says "on this machine, that provider is already paid for". An entry for a provider this machine has no login for is written as declared and simply offers nothing: the example above is a shape to copy, not a list to paste whole.

Going back is deleting the key: the next `nexgen openclaw setup` writes the reachable wildcards again and the doctor line has nothing left to refuse. An entry that is not a provider id is ignored and named in one line, like a ceiling that cannot be used; a value that is not a list at all costs the whole guard, and the line says so instead of reading as "nothing to restrict".

## The doctor line

`nexgen doctor` prints one line, `quota.known`, listing every percentage that is a real number.
It is `OK` normally and `WARN` when one of them has reached 80%, with the action `Run 'nexgen quota' to see every window.`.
It never turns red and never changes the exit code: a plan close to its ceiling is a fact to act on, not a broken machine.
A machine where nothing is readable says so in one sentence instead of an empty line.
Those percentages may come from the optional tokscale backend, and the doctor asks it from the same ten-minute cache: a doctor run, a beat and a command typed in the same ten minutes pay for one run of it between them, not three.

## What it deliberately does not do

- The engine itself never calls a provider's API. No endpoint publishes what is left, and querying one to find out would be a new way to be wrong.
- It keeps no history and makes no forecast. Every run reads the present.
- It does not treat a stale reading as a current one. A reading a provider refused to refresh keeps its number and its age and loses its percentage, so the last known value is never mistaken for a current one and no handover acts on it.
- The engine itself reads no credential file to obtain a number. The optional tokscale backend does exactly that, which is why it is a separate binary you install, and why `backends: {tokscale: off}` exists.
- The engine itself does not call remote quota endpoints, such as Claude Code's plan-usage endpoint or Google Cloud Code's `fetchAvailableModels`, with user credentials. The optional tokscale backend calls them on your behalf with the login already stored on this machine, and its numbers are the provider's own answers, not this engine's arithmetic.
- No number from the backend is trusted beyond its shape: the fields are validated, the age is printed, and an unrecognised format is refused instead of interpreted.
