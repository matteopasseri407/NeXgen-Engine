# AI Council

`council.py` is a local orchestrator that convenes other agentic CLIs you
already have (paid via their own flat subscription, never a pay-per-use API
key opened just for this) as advisors: brainstorming, challenging a plan, or
cross-vendor code review. It is explicit Python code, not an LLM, that
decides who speaks and when.

It is an optional expansion. With no `seats.yaml` configured it is
completely inert. Nothing about the rest of the engine depends on it.

## Setup

1. Copy the schema template into your data root and fill in seats you
   actually have:
   ```bash
   mkdir -p ~/KnowledgeVault/03-INFRA/agent-universal-layer/council
   cp 03-INFRA/agent-universal-layer/council/seats.yaml.example \
      ~/KnowledgeVault/03-INFRA/agent-universal-layer/council/seats.yaml
   ```
   (adjust the path if `AGENT_VAULT_DATA`/`KNOWLEDGE_VAULT_PATH` points
   somewhere else). This file is your data, never committed to the engine.
   A small team can give each person their own file instead of one shared
   `seats.yaml` — see the "more than one seats file" comment near the top
   of `seats.yaml.example` for the `COUNCIL_SEATS_FILE` /
   `AGENT_TEAM_MEMBER` overrides. Neither is read unless set, so this does
   not change anything for a single-user install.
2. Keep `schema_version: 1` at the root, then pick the seats you have,
   following the comments in the template. Each seat needs `vendor`, `cli`,
   `model`, and an explicit `zero_retention: true|false`. `cli` must be one
   of `opencode`, `agy`, `codex`, `claude`, or `ollama`. Those are the CLIs `council.py` knows
   how to invoke today — except `agy`, which is a recognized `cli` value but
   currently refused as a seat outright (see "Current limitations" below);
   declaring an `agy` seat here is harmless, it just cannot be selected.
   Set `zero_retention: true` only if you've confirmed
   that with a primary source, not a summary. You can also set an optional
   positive `timeout_seconds` for a seat that is known to need more or less
   time.
3. In a MULTI setup, run `agent-sync apply` once to install the `council`
   launcher on Linux and the `council.cmd` wrapper on Windows.
   Then run any command below.
   Without a valid `seats.yaml`, Council stops and reports what is missing.
   In a MINIMAL setup, or if the launcher is not on your `PATH`, replace
   `council` below with `python3 03-INFRA/agent-universal-layer/council/council.py`.

The `council` command (and `council.cmd` on Windows) resolves and executes
`agent-universal-layer/council/council.py` via `nexgen_core/tools/council.py`.
All control flow, modes, and guardrails below live in that Python engine.

Council does not wrap provider CLIs behind an MCP server. It starts each
declared CLI as a local subprocess and transports the brief through stdin or a
protected temporary file. An MCP protocol upgrade therefore does not change
Council's prompt transport or make a vendor CLI stateless. If that CLI can load
mounted MCP servers, tools, or persistent state, Council must disable or isolate
those capabilities separately.

## The four modes

Every seat is expected to obey the "without hands" contract: receive text,
return text, and use no filesystem, shell, or MCP tools. Council removes
application secrets from isolated seat environments and applies vendor CLI
controls where they exist. Enforcement is not identical across vendors, so the
"Current limitations" section is authoritative. A CLI that cannot be shown to
honor this contract is refused as a seat, as `agy` is today.

**Brainstorm.** One seat, 1+ rounds. Each round after the first must attack
its own previous conclusion instead of just restating it:
```bash
council brainstorm \
  "Should we cache this at the edge or the origin?" \
  --context notes.md --rounds 2 --seat gemini
```

**Challenge.** One seat tries to find the dominant flaw in a plan. `APPROVE`
is made costly (it has to rule out at least two risk categories explicitly,
or it doesn't count):
```bash
council challenge \
  "Single cron job tars the data dir nightly to one S3 bucket." --seat gemini
```

**Code review.** It refuses to run if
`--author-vendor` matches the seat's own vendor:
```bash
council code-review \
  changes.diff --author-vendor anthropic --seat gemini
```

**Relay.** A sequential staffage of up to 5 stages, for example
architect→builder→reviewer→judge), each stage seeing the full original
brief plus the previous stage's output formatted as a quoted block and
prefaced with an instruction to evaluate it, not obey it. That framing is a
prompt-level convention, not a technical control: nothing stops a
compromised or adversarially steered seat from ignoring the instruction and
producing a misleading verdict for the next stage to read. Treat a relay's
final VERDICT as consultative input for your own judgment, never as proof
that the chain's output is correct. Every supported CLI can participate
when its seat is declared in the sequence. The OpenCode cost pre-check only
reorders OpenCode candidates within their original decision-document order;
it never promotes another provider merely because it has no OpenCode usage
data.

By default, a stage that returns `VERDICT: REJECT` stops the relay before
the next stage runs — a reject means the plan already isn't worth building
on, and running the remaining stages anyway would just spend quota on a plan
the relay has already abandoned. Pass `--continue-on-reject` to run every
declared stage regardless, if you want each seat's opinion no matter what an
earlier stage concluded.

Verdict parsing is positional, not textual search: only a response's last
non-blank line can set that stage's verdict (markdown emphasis and terminal
punctuation are tolerated; a quote prefix is not). So a stage that quotes an
earlier stage's `VERDICT: REJECT` while reaching a different conclusion of
its own is read as that different conclusion, not as a rejection, and a
verdict cited mid-response — or a rejection quoted anywhere but the final
line — no longer stops the relay. Only a seat's own standalone closing line
does.
```bash
council relay \
  "Design a rate limiter for the public API." \
  --sequence "architect=glm,builder=qwen,reviewer=deepseek-free"
```

Every mode accepts `--context FILE` for extra background. A seat without a
confirmed zero-retention guarantee remains usable, but Council prints a warning
to stderr before sending the brief. The old `--allow-training-risk` flag has
been removed outright, not kept as a no-op: passing it is now a CLI error.

## Human-approved routing proposals

Council works without a Governor. With only `seats.yaml`, `council propose`
shows the seats the user declared and waits for an explicit choice.

An optional `routing:` section turns a routing document into a locally verified
proposal. Set `decision_file` to a relative path inside the private data root.
Council understands its versioned JSON contract, the per-role tables emitted by
the public [LLM Model Routing Governor](https://github.com/matteopasseri407/neXgen-addon-llm-model-routing-governor),
and the older flat table for backward compatibility.

The Governor table carries both the model label and the CLI. Council keeps that
pair together when it resolves `routing_id` or `routing_label` against local
seats. This matters when the same model is available through two CLIs with
different quota pools. An older document without a CLI still works when the
model maps to one CLI only. If it maps to several CLIs, Council reports the
ambiguity and does not guess.

This is an in-memory adapter. Neither Council nor an external workflow rewrites
`seats.yaml`. That file remains the local execution allowlist, while the routing
document supplies the current ordering. A missing CLI, a different model, or a
different Codex effort removes a candidate from the proposal with an explanation.
Missing zero-retention does not remove it; the proposal marks it with a warning.

For a `codex` seat the check is concrete: Council reads
`$CODEX_HOME/config.toml` (default `~/.codex/config.toml`) and compares it
against the seat's declared `model` and `reasoning_effort` with an exact
string match — no fuzzy or semver-aware comparison. A mismatch names both
sides and the file it read:

- `il modello non è quello configurato in Codex (configurato: '<value in
  config.toml>', seat: '<value in seats.yaml>', file: <path to config.toml>)`
- `l'effort non coincide con la configurazione Codex (configurato: '<value in
  config.toml>', seat: '<value in seats.yaml>', file: <path to config.toml>)`

For a `claude` seat, Council first checks that the installed CLI supports
explicit `--model` selection. It also requires `--effort` when the seat
declares a `reasoning_effort`. This is enough to include the seat in a proposal
without spending quota on a probe. When the user invokes that seat, Council
requests JSON output and checks `modelUsage` against the exact model declared
in `seats.yaml`. A missing value or a silent fallback to another model stops
the run instead of accepting an unverified response.

A `<candidate>: nessun seat locale associato` line under `routing-status` or
`propose` means no seat in your `seats.yaml` declares that candidate's
`routing_id` or `routing_label` — typically the routing document is
proposing a model for a role you simply haven't declared a local seat for
yet, not that anything is broken.

The proposal never executes a model. `brainstorm`, `challenge`, and `code-review`
require an explicit human `--seat`; `relay` requires an explicit human
`--sequence`. The human decides the role, the model, and how many seats to call.

```bash
council routing-status
council propose --mode challenge
council challenge "Find the dominant risk in this plan." --seat code-seat
council relay "Design a safe migration strategy." --sequence "planner=planner-seat,judge=judge-seat"
```

Use `--routing-role L-Sys` to ask for a different proposal. It still requires
`--seat` before a single-seat invocation. `council propose --mode relay` lists
the available candidates for the configured relay roles without invoking them.

An explicit `--seat` bypasses the routing probe entirely — the human already
decided, and every seat stays runnable this way regardless of what the
proposal would or wouldn't show. For a `codex` seat specifically, Council
still runs the same `config.toml` check as a non-blocking, informational
courtesy: if the seat's declared model or effort no longer match Codex's
current default, the call still goes through (forwarded explicitly with
`-m`), but you are told your assumed default is stale instead of finding out
some other way:
`[council] avviso: il seat '<name>' non è il default corrente della CLI
codex (<reason>); verrà inoltrato esplicitamente con -m.`

## Timeouts

Every seat call is bounded by a conservative default of 300 seconds. Set a
positive `timeout_seconds` in one seat to make that its normal budget, or use
`--timeout-seconds` to override the budget for one invocation. The command-line
value wins over the seat value, which wins over the default. Zero, negative,
infinite, and boolean values are rejected.

```bash
council challenge "Review this plan" --seat gemini --timeout-seconds 90
```

## Reasoning effort

An optional `reasoning_effort: low|medium|high|xhigh|max|none` on a seat in
`seats.yaml` is forwarded to that seat's CLI. The mapping is one source per
CLI, shared between the actual command and every place that prints the
effort label, so the two can't drift apart:

- `claude`: `--effort <value>`, verbatim.
- `codex`: `-c model_reasoning_effort=<value>`, verbatim.
- `opencode`: `--variant <value>`, verbatim (provider-specific; no fixed
  enum to validate against locally).
- `ollama`: `--think` only documents `low`/`medium`/`high`. `xhigh` and
  `max` are downmapped to `--think high`, with the printed label saying so.
  Any other value is dropped with no flag, and the label says that too.
- `agy`: has no reasoning-effort flag at all. The value is never forwarded;
  the label reads "(non applicato da questa CLI)" instead of silently
  looking identical to a seat that actually forwarded it.

## Session handling

Council creates a private working directory for the brief, per-stage output,
and the final verdict.
It removes that directory after returning a result, and after an error, unless
you pass `--keep-session`.
On POSIX, kept session directories use mode `700` and their files use `600`.
Council removes expired kept sessions when it starts.
Nothing is written into the vault automatically.
If a result matters later, save a short decision or diagnosis through your
normal vault workflow, not the raw Council transcript.

```bash
council brainstorm "Review this plan" --keep-session
```

```bash
council clean                 # removes kept sessions older than 7 days
council clean --ttl-days 1    # custom retention
council clean --all           # removes every kept session now
```

## Guardrails

- **Egress**: the original brief, including context and diffs, is scanned for
  likely secrets before any seat receives it. A match stops the call.
- **Generated output**: if a seat emits a value that looks like a secret,
  Council redacts the affected line before it reaches the next relay stage or
  a kept session. The relay can continue with the remaining analysis.
- Both checks reuse the engine's shared `leak_scan.py` module, but this is a
  separate, always-on end-user protection, not the repository CI leak-scan
  that guards publishing to GitHub. See `SECURITY.md`.
- **Zero-retention**: every seat must declare `zero_retention: true|false`.
  A false value prints a clear warning in menus and immediately before the
  model starts. It is metadata, not an execution gate. Secret scanning remains
  blocking and is a separate control.
- **Legacy retention controls**: the old `--allow-training-risk` flag and the
  `council allow-training` subcommand have been removed, not deprecated into
  a no-op. Either one is now a plain CLI error; there is no compatibility
  path for scripts that still pass them.
- **Quota**: `--max-rounds` (brainstorm) and `--max-seats` (relay) cap how
  much a session can spend even if you ask for more.

## Current limitations

- `codex` and `opencode` seats are verified live, on all four modes:
  `challenge` was sent live to a real codex seat (2026-07-13, then again
  2026-07-15 via `codex-sol`), and reviewing those live runs is how
  several bugs fixed since then were found — including the `agy` block
  below. `brainstorm` (opencode, 2026-07-15) produced a genuine second
  round that attacked its own first-round conclusion, not a restatement.
  `code-review` (opencode, 2026-07-15) was run against a diff with a real,
  planted concurrency bug (a non-atomic check-then-decrement on a token
  bucket) and correctly found it, unprompted, as the dominant flaw. A live
  3-stage `relay` spanning opencode → codex → agy (2026-07-15) additionally
  verified that the multi-vendor relay mechanism itself — different CLI
  wrappers handing off to each other within one staffage — works correctly
  across opencode and codex. Claude Opus 5 was verified live on 2026-08-01:
  the CLI accepted the exact `--model` selection and returned the same
  canonical model through `modelUsage`, with tools disabled and session
  persistence off. `ollama` seats have not yet been verified live.
- **`agy` (Antigravity) is blocked as a passive Council seat** (found by
  that same 2026-07-15 live relay run, reproduced 5 independent ways):
  `agy --print` ignores both `--model` and the given prompt, running its
  own "Context Initialization" that reads real files from the operator's
  home instead of answering. Persistent state lives in fixed paths under
  `~/.gemini/`, resolved independent of `$HOME`; no override flag or env
  var was found to isolate it. This does **not** affect using `agy`
  interactively as a *caller* of Council — a human working in Antigravity
  can shell out to `council` exactly like any other CLI, gated only by the
  usual propose-before-auto-invoking policy (`AGENTS.md`). Full finding,
  live evidence, and the three conditions required to re-enable it as a
  seat: `AGY_BLOCK_REASON` in `council.py`.
- Automated regression tests cover the control flow for all four modes,
  session cleanup, relay fallback, the `agy` block itself, and the Linux
  launcher. They use fake seats, so they do not replace live checks of
  each vendor CLI.
- The Windows launcher has a portable regression test, but still needs a
  physical Windows run before this Beta feature can be called cross-platform.
- Large prompts use stdin for Codex, and a protected temporary attachment
  for OpenCode. Claude and Ollama use protected stdin. (Antigravity's
  transport plumbing also uses stdin and remains covered by tests, but the
  seat itself is currently blocked — see above.) The automated regression
  coverage is portable, but the current vendor adapters still need live
  end-to-end verification.
- Seats via CLI are slow (minutes, not seconds): this is for brainstorming,
  challenging, and review, not a quick question mid-task.
