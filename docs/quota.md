# The quota: what is left of each plan

No provider offers a local answer to "how much of this plan is left".
The engine does not invent one.
It reads what the harnesses on this machine have already written about their own usage, repeats the numbers they give, and says plainly which windows cannot be measured from here.

`nexgen quota` prints one row per provider and window.
`/quota` prints the same rows inside OpenClaw.

## The three surfaces

| Surface | What it gives |
| --- | --- |
| `nexgen quota` | one line per row: provider, window, what was used, the percentage when it is a real one, the reset when the provider gave it, and how old the reading is |
| `nexgen quota --json` | the same rows as data, plus `lines`: the text already rendered, in the machine's language |
| `/quota` in OpenClaw | the `lines` of the JSON, forwarded to the chat |

The strings are formatted once, by the engine.
The plugin never turns a number into a sentence and never rounds a percentage, so the chat and the terminal cannot disagree about the same reading.
The command always exits 0: a source that cannot be read is an answer, not a failure.

## What each provider gives

| Provider | Where the reading comes from | Windows | Percentage | Reset |
| --- | --- | --- | --- | --- |
| Codex | the last `rate_limits` event in the newest `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `5h`, `7d`, `30d`, as the event declares them | yes, `used_percent`, repeated as given | yes, `resets_at` |
| OpenCode Go, OpenCode Zen | the harness archive `~/.local/share/opencode/opencode.db`, spend per session split by the provider that was paid | `5h`, `7d`, `30d` | only when `quota.yaml` declares a ceiling for that window | no |
| Claude Code | `message.usage` in `~/.claude/projects/*/*.jsonl` | `5h`, `7d` | never, the plan's limit is not in any local file | no |
| Antigravity | local quota extension logs `*-Antigravity Quota.log` under `%APPDATA%\Antigravity IDE\logs` (prompt credits) | `30d` | yes, when a local log exists; otherwise not measured | no |

### Codex

Codex writes a token-count event into its session file, and that event carries the plan's own `rate_limits` block: the percentage used, the length of the window in minutes, and when it resets.
The engine reads the tail of the newest session, at most 8 MiB, and repeats the numbers without recomputing them.

A reading older than the window it describes is printed as stale and its percentage is dropped.
After a window has rolled over the old percentage describes a period that has already reset, and repeating it would be worse than saying nothing.

### OpenCode

The spend is what the harness archived, split by the provider recorded on each session: `opencode-go` (the flat subscription) and `opencode` (Zen, pay per use).
A model id ending in `-free` costs zero, so a free lane appears as `0.00 $` instead of disappearing: a plan with no cost is a fact worth one line.

The arithmetic is the harness lane's own, in `nexgen_local/supervision_spend.py`.
The quota report decides what a person reads, never how a cost is computed.
An engine installed without that lane says so in one line and shows the other sources.

### Claude Code

`nexgen quota` sums the `message.usage` counters of the transcripts written in the last seven days, split into a 5h and a 7d window, deduplicated by message id.

It prints token counts and no percentage, because Claude Code's plan usage is not in any local file (`~/.claude` settings, cache, or transcripts): `/usage` queries Anthropic's remote plan-usage endpoint dynamically.
The line says so: `the limit is not readable on this machine`.
That is the honest answer, and it is deliberately not turned into a guess.

The transcripts are read by this command only.
They were 613 MB in eight days on the machine this was written on, which is affordable for a command someone typed and not for an hourly timer: the beat and the doctor use the cheap sources instead.

### Antigravity

Antigravity's desktop environment logs prompt credits usage via the `ag-quota` extension into local session logs (`*-Antigravity Quota.log` under `%APPDATA%\Antigravity IDE\logs` or `%APPDATA%\Antigravity\logs`). The engine reads the newest log tail and extracts the monthly prompt credits used percentage for the `30d` window, dropping it as stale if older than 30 days. When no local log exists on disk (such as on a machine using only the `agy` CLI, where quota is fetched in-memory from the Google Cloud Code API without being written to disk), its row reports `not measured`.
It exists so the list is complete and nobody has to wonder whether it was forgotten.

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
The thresholds live in one place, `quota.ALERT_AT`, and the alert and the doctor read the same tuple, so they can never disagree about what "high" means.

The alert is raised by the beat that already runs every hour, on both platforms, right after the dependency watch.
It goes through the megaphone, like every other alert, and carries the provider, the window and the reset when the provider gave one.

The debounce key contains the band: `quota:<provider>:<window>:80` and `quota:<provider>:<window>:95`.
Keying on the provider alone would let the 80% alert silence the 95% one, which is the alert that matters most.
The megaphone's own four-hour debounce applies on top: one missed repeat is cheaper than an alert every hour on a plan that is simply full.

Only the cheap readings are consulted.
The Claude Code transcripts are never opened by a timer.

## What a plan hands over to

The `failover:` section of the same `quota.yaml` is where you declare what a turn should use when a plan runs out, and in which order.
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

The model shown is the first entry of the assistant's chain that runs on a different provider: a spent plan takes its own sibling models with it.
A provider with no chain declared prints its row as it always did, and no handover is invented.

`nexgen openclaw setup` is what carries the chains to OpenClaw: see [the OpenClaw page](openclaw.md#the-fallback-chain).
`nexgen doctor` prints one line, `openclaw.fallbacks`, with how many agents carry a chain and which ones carry none.

## The doctor line

`nexgen doctor` prints one line, `quota.known`, listing every percentage that is a real number.
It is `OK` normally and `WARN` when one of them has reached 80%, with the action `Run 'nexgen quota' to see every window.`.
It never turns red and never changes the exit code: a plan close to its ceiling is a fact to act on, not a broken machine.
A machine where nothing is readable says so in one sentence instead of an empty line.

## What it deliberately does not do

- It never calls a provider's API. No endpoint publishes what is left, and querying one to find out would be a new way to be wrong.
- It keeps no history and makes no forecast. Every run reads the present.
- It does not treat a stale reading as a current one, and does not carry the last known percentage forward.
- It does not read any credential file to obtain a number.
- It does not call remote quota endpoints (such as Claude Code's plan-usage endpoint or Google Cloud Code's fetchAvailableModels) with user credentials to fetch quotas dynamically.
