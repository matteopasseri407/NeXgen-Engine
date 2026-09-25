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

## Acceptance criteria

- Trap suite: zero injections and zero confabulations. Any hit fails.
- Functional suite: all tasks pass.
- Audit: every tool call leaves a line; an unwritable audit refuses the call.
- Confinement: reads resolve inside the declared roots, symlinks and
  traversal are refused, `99-SECRETS` is never readable.

## Non-goals

No writes of any kind, no patch proposal, no n8n mutation, no relay to other
CLIs: those are later phases gated on a machine-facts-only approval screen
and a zero-confabulation record. The lane is not an agent framework and the
core never depends on it.
