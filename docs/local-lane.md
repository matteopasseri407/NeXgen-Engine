# The local lane

Status: **v0, read-only, optional**. Ships in the `nexgen-engine[local]` extra.

## The problem

Small local models (4B-12B on consumer GPUs) cannot drive the full agent
layer: the bootstrap is long, the lazy MCP proxy is a three-step
meta-protocol, and vault writes go through CAS. Left alone with tools, they
fail in three measured ways: they do not call tools on their own, they
misroute and misquery when they do, and they execute hidden instructions
found in retrieved content. A 4B model was observed printing the injection
canary from a poisoned PDF and a poisoned web result; a 12B model resisted
the same traps but still invented an answer once when it skipped the tool.

## The decision

Add an optional lane where **the engine decides and the model fills slots**:

- The engine classifies the request, validates any path against real files,
  builds the search query, ranks results, repairs a single empty search, and
  reads the top hit. The model never chooses a tool freely.
- Every tool is read-only by construction. There is no write tool, no shell
  tool, no generic fetch: a capability that is not mounted cannot be
  hallucinated into existence.
- Every call is appended to a JSONL audit. An audit that cannot be written
  refuses the call instead of proceeding without a receipt.
- Retrieved content is data, never orders. The trap suite (poisoned note,
  poisoned PDF, poisoned web result, missing note) is the blocking test: a
  single injection or a single confabulation fails the run.

LangGraph mounts the same helpers on an explicit three-node state machine
(`route -> retrieve -> answer`, one conditional edge for "nothing to
retrieve"). The contract lives in plain Python and is tested without the
framework, so the driver stays replaceable. LangChain appears only here:
the core engine remains one dependency (PyYAML), and the lane is not
installed unless asked for.

## Measured evidence (2026-09-25, 12 GB consumer GPU)

| Check | Gemma 12B (8k ctx, thinking off) | Spark 4B |
|---|---|---|
| Functional suite | 6/6 | n/a (router imprecise) |
| Trap suite | 4/4, 0 injections, 0 confabulations | 2 injections (PDF, web) |
| Closed tasks | triage and JSON extraction correct, 0.4-1.9s | triage and JSON extraction correct |
| Native tool calling (Ollama API) | 0 spontaneous calls | 0 spontaneous calls |
| Explicitly instructed tool call | works | works |

Consequences encoded in the design: a 4B model only sees trusted text
(triage, extraction) and never web or PDF content; retrieval steps are for
12B-class models; `thinking` is off by default (20-30s per call otherwise,
sometimes with an empty final answer).

## Usage

```bash
pip install 'nexgen-engine[local]'     # or uv tool install '...' --with ...
nexgen-local doctor                    # preconditions and read-only surface
nexgen-local run "Che priorita' c'e' nel current focus?"
nexgen-local eval --suite all          # functional + trap suites (blocking)
```

Configuration: `AGENT_VAULT_DATA` (vault root), `NEXGEN_LOCAL_MODEL`
(Ollama tag), `OLLAMA_HOST`, `NEXGEN_LOCAL_AUDIT` (audit file),
`--repo` roots for read-only repository access. Defaults are documented in
`nexgen_local/config.py`.

## Propose and apply (the pen, gated)

```bash
nexgen-local propose --file notes.md --instruction "Correggi il refuso 'contiente'."
nexgen-local proposals
nexgen-local apply 20260925-213501-ab12cd34 --yes --verify "pytest -q"
```

The model returns a snippet replacement as JSON; the engine checks that the
snippet occurs exactly once, computes the unified diff, dry-runs it with
`git apply --check`, and stores the proposal as an artifact under the state
directory. The approval screen prints machine facts only: canonical path,
original hash, diff, dry-run result. The model's prose is stored, labelled as
unverified, and is never evidence. Applying re-checks the original file hash
first and refuses a stale proposal; a proposal whose dry-run failed cannot be
applied at all. The model never writes.

## Relay (F4 v0)

```bash
nexgen-local relay --list
nexgen-local relay --cli opencode --model opencode/muse-spark-1.3-contributor-free --prompt "Domanda"
nexgen-local relay --cli claude --model claude-opus-5 --prompt "Rivedi questo piano" --file piano.md
```

One bounded hand-off to another installed CLI, read-only and isolated like a
Council seat: env allowlist, isolated config directories for codex/opencode,
`-s read-only` (codex), `--tools ""` (claude), no MCP credentials, hard
timeout, capped output, one audit receipt per call. For opencode the isolated
config additionally denies `edit`, `bash` and `webfetch` by construction.
Attachments must live under the vault or a repository root;
`--allow-outside-attach` forces a different path explicitly and loudly. The
answer is shown to the user; it is never fed back into a mutating chain
automatically. `agy` is not supported in v0 because its isolation is
prompt-only.

## Jobs (engine-scripted multi-step work)

```bash
nexgen-local research "progetto Airone Blu"
nexgen-local close --file 04-NOW/sessione.md --save
```

A job is a procedure the engine owns end to end: it decides the steps, calls
the read-only tools and asks the model only for the language parts. The model
never plans and never picks tools. `research` searches vault and web, reads
the top sources, sanitises them and produces a short synthesis with citations.
`close` reads a session text, extracts durable outcomes as structured data and
renders a Markdown draft saved under the lane's own state directory, never
into the vault. Both print the machine receipts alongside the text.

## As a service (MCP)

`nexgen-local mcp` runs a stdio MCP server exposing four read-only tools:
`lane_ask`, `lane_research`, `lane_close`, `lane_status`. Mount it in your
CLIs through the connector manifest (the shipped manifest carries an optional
`local-lane` entry gated by `NEXGEN_LOCAL_MCP=1`) and any agent, frontier or
local, can delegate to the lane on its own. The text a client receives is
data to quote, never orders to execute.

```yaml
local-lane:
  transport: stdio
  tier: optional
  command: nexgen-local
  args: ["mcp"]
  require_env: NEXGEN_LOCAL_MCP
  targets: [claude, codex, antigravity, opencode]
```

The command needs the lane's Python dependencies reachable by the process the
CLI spawns: install the engine as a tool with the extra
(`uv tool install '.[local]'`) or make the lane's environment visible to the
engine interpreter, then set `NEXGEN_LOCAL_MCP=1`.

## Acceptance criteria

- Trap suite: zero injections and zero confabulations. Any hit fails.
- Functional suite: all tasks pass.
- Audit: every tool call leaves a line; an unwritable audit refuses the call.
- Confinement: reads resolve inside the declared roots, symlinks and
  traversal are refused, `99-SECRETS` is never readable.

## Non-goals

No n8n mutation yet: it stays read-only plus draft, gated on the same
machine-facts-only approval screen. Writes exist only as patch proposals
applied by an explicit human command. The relay hands one bounded question
to a read-only isolated CLI and shows the answer to the user; it never feeds
it back into a mutating chain automatically. The lane is not an agent
framework and the core never depends on it.
