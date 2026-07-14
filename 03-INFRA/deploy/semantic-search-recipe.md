# Semantic search backend — build recipe (not a deployable stack)

This is a **specification**, not source code. Nothing in `03-INFRA/deploy/`
runs this for you — there is still no `semantic-search/` compose stack next
to `firecrawl/`, `ocr/`, and `n8n/`. What follows is a complete, unambiguous
description of a backend that satisfies the `vault-library` MCP's
`semantic_search` contract (see `03-INFRA/deploy/vault-mcp/src/vault_mcp_server/config.py`
and `server.py`) and reproduces the exact retrieval architecture, weights,
and models the maintainer's own production instance runs. An AI coding agent
with no other context should be able to implement a working, drop-in
replacement from this document alone.

Every numeric weight, model name, and resource figure below is the real
production configuration, not a placeholder. Where the recipe leaves a
choice open (which HTTP framework, which BM25 library, exact chunk size), it
says so explicitly — that plumbing doesn't affect the contract or the
ranking behavior, so it's yours to pick.

## 1. Where this plugs in

`vault-mcp` already contains everything needed on its side:

- `SEMANTIC_URL` — base URL of your backend, e.g. `http://vault-semantic:8080`.
- `SEMANTIC_ENABLED=true` — turns on the `semantic_search` MCP tool.
- `SEMANTIC_MAX_LIMIT` — caps the `k` a caller can request.

When `SEMANTIC_ENABLED` is true and `SEMANTIC_URL` is set, `vault-mcp` calls
`GET {SEMANTIC_URL}/search?q=<query>&k=<limit>` and returns your JSON
response verbatim to the agent (after an optional path-prefix filter it
applies itself). If your backend is down or errors, `vault-mcp` catches the
exception and returns `{"error": "semantic_unavailable", ...}` — it never
crashes the MCP server. You do not need to change anything in `vault-mcp`;
you only need to implement a service that speaks this contract.

## 2. HTTP contract (must match exactly)

### `GET /search?q=<string>&k=<int>`

Response body:

```json
{
  "query": "<the query you received>",
  "results": [
    {
      "path": "relative/path/to/note.md",
      "title": "Note title",
      "score": 0.00236,
      "snippet": "≤ ~300 characters of the matching chunk, for the agent to read"
    }
  ]
}
```

`path` is load-bearing: `vault-mcp` filters on it when an `INCLUDE_PATH_PREFIXES`
allowlist is configured. `title`, `score`, and `snippet` are not mechanically
required by `vault-mcp`, but you need all four for behavioral parity —
without a `title`/`snippet` the calling agent gets an unusable hit, and the
title is a required *input* to your own ranking pipeline (§4).

### `GET /health`

Response body:

```json
{ "status": "ok", "reranker": true, "chunks": 1284, "files": 202 }
```

`status` and `reranker` are the two fields worth alerting on — `reranker`
lets you tell at a glance whether the service degraded to fusion-only mode
(§5) without a log dive. `chunks`/`files` are for humans watching the
index size over time.

## 3. Embedding model — exact, do not substitute

**`minishlab/potion-multilingual-128M`** via the `model2vec` Python package
(`pip install model2vec`). This is a static-embedding model: no attention,
no GPU, sub-millisecond encode per chunk, runs on any CPU including ARM.
Load it once at boot; keep it resident in memory. There is no meaningful
alternative to swap in for parity — the specific distillation is what makes
full reindexes cheap enough to run on a timer instead of incrementally.

## 4. Contextual chunking

Before embedding **and** before lexical indexing, prepend the note's title
and filename to every chunk's text:

```
"{title} — {filename}\n\n{chunk_text}"
```

This is not cosmetic. The corpus leans hard on the title/filename signal
(see the `W_TITLE` weight below); a chunk embedded without that context
underperforms measurably, and the same fix is required again at rerank time
(§5). Split notes into chunks at a paragraph/section granularity — exact
chunk size is your choice; the production corpus (~202 files) reindexes
fully in 1–6 seconds at whatever granularity it uses, which is the bar to
stay under.

## 5. Retrieval pipeline — exact algorithm

Four stages, in order. Stages 1–3 always run; stage 4 is a rescoring pass
over the winners of 1–3.

**Stage 1 — vector search.** Cosine similarity between the query's
`model2vec` embedding and every chunk's embedding. Rank the corpus by this
score.

**Stage 2 — lexical search (BM25).** Standard BM25 over the same
(contextualized) chunk text. Any implementation is fine — an in-process
BM25 library, or a SQL full-text index with a BM25 ranking function both
work; the production instance stores everything in a single SQLite file
(embeddings as blobs + a BM25-capable text index) so reindex and query stay
in one process with no extra service to run.

**Stage 3 — title/filename boost.** A third, independent lexical ranking
that matches the query **only** against each chunk's `title`/`filename`
(not its body). This is what lets an exact-name query outrank a
semantically-similar-but-wrong note.

**Fusion — Reciprocal Rank Fusion (RRF).** For each of the three ranked
lists above, a candidate at rank `r` (1-indexed) in that list contributes
`weight / (RRF_K + r)` to its fused score. Sum the three contributions per
candidate, sort descending, keep the top `TOPN` as the candidate pool.

Exact weights (env-configurable, these are the production values):

| Env var | Value | Meaning |
|---|---|---|
| `W_VEC` | `1.0` | vector-search weight |
| `W_BM` | `0.4` | BM25 weight |
| `W_TITLE` | `0.6` | title/filename-boost weight |
| `RRF_K` | `10` | RRF rank-damping constant |
| `TOPN` | `50` | candidate pool size handed to stage 4 |

Setting `W_TITLE=0` (or any weight to `0`) is a valid, supported way to
disable that signal — treat it as a first-class rollback lever, not a
special case to special-case in code.

**Stage 4 — cross-encoder reranker.** Re-score every candidate in the
`TOPN` pool with a cross-encoder, then cut to the final `k` by the
reranker's order (not the fusion order). This is the stage that makes the
setup "the most performant we run," not an optional add-on:

- **Model:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual,
  trained on mMARCO which includes Italian).
- **Export:** convert to ONNX and apply dynamic int8 quantization
  (471 MB → 118 MB on disk). Ship the exported `model.int8.onnx` +
  tokenizer files inside your image; do not re-export at container start.
- **Reranker input — the detail that matters most:** do **not** feed the
  cross-encoder a bare snippet. Prepend `"{title} — {filename}"` to the
  snippet first, exactly as in the embedding step (§4). A bare-snippet
  first attempt measurably *hurt* results (hits@5 dropped from 15/20 to
  12/20 on a 20-query gold set); prefixing title/filename brought it to
  16/20, hits@1 13/20, MRR 0.702. Skipping this prefix is the single most
  likely way to get a "working" reranker that quietly performs worse than
  no reranker at all.
- **Pool and snippet size — tune for latency, not just quality:** pool of
  20 candidates / 480-char snippets measured ~860–900 ms per query on a
  4-core ARM box — too slow for interactive use. Pool of **10** candidates
  and **240-char** snippets brought it to ~150–250 ms/query with equal or
  better quality (the RRF fusion stage already surfaces the right
  candidates into the first 10; widening the pool bought nothing).
- **Threads:** explicitly set the ONNX Runtime session's
  `intra_op_num_threads` to the machine's CPU count. The runtime's default
  does not use all cores; setting this explicitly roughly halved inference
  time in production (166 ms → 98–112 ms per batch on a 4-core ARM VM).
  This is easy to miss because the service still "works" without it — it
  just silently runs at half speed.

Env vars for this stage:

| Env var | Default | Meaning |
|---|---|---|
| `RERANK_ENABLED` | `1` | set `0` to skip stage 4 entirely (pure RRF+title-boost) |
| `RERANK_POOL` | `10` | candidates rescored by the cross-encoder |
| `RERANK_SNIPPET_CHARS` | `240` | snippet length fed to the reranker (after the title/filename prefix) |

**Degrade, never crash:** if the reranker model fails to load at boot
(missing file, corrupt export, out-of-memory), log it and fall back to pure
RRF+title-boost — the same behavior as `RERANK_ENABLED=0` — rather than
failing the whole service. `GET /health` should report `"reranker": false`
whenever this fallback is active, whether by config or by failed init, so
it's visible without reading logs.

## 6. Reindexing

Because `model2vec` embedding is cheap, a **full** reindex (drop and
rebuild, not incremental diffing) is the simplest correct design and is fast
enough to run on a timer: ~1–6 seconds for ~200 files / ~1,300 chunks.
Reindex on an interval (`REINDEX_INTERVAL_SECONDS`, default `900`) by
comparing file mtimes against the last indexed state; skip files whose
content hasn't changed.

Exclude these from indexing (env `EXCLUDE_PATH_PREFIXES`, comma-separated —
same env var name and semantics `vault-mcp` already uses for its own
`INCLUDE_PATH_PREFIXES`/`EXCLUDE_PATH_PREFIXES`, so the two services can
share one mental model): your secrets directory (e.g. `99-SECRETS`), VCS
metadata (`.git`), editor metadata (`.obsidian` or equivalent), and any
generated/build output directories that duplicate canonical content under a
second path — indexing a dead build artifact next to the live note it was
built from actively confuses a per-content judge like the cross-encoder
reranker, since it now sees the same content twice under different paths.

## 7. Packaging and resource footprint

Single container, CPU-only, no GPU, no external dependencies (no Redis, no
Postgres — everything lives in one process plus a local SQLite file). This
is the whole reason it fits comfortably inside the same free tier this repo
already targets (see the top-level README: **Oracle Cloud Always Free**, 4
ARM Ampere cores / 24 GB RAM / 200 GB SSD, mentioned once and reused by
every stack in this directory).

Measured production footprint on that hardware:

| Configuration | RAM in use |
|---|---|
| Stage 4 (reranker) disabled | ~1.2 GB |
| Stage 4 (reranker) enabled | ~1.6 GB |

Recommended container memory cap: **2 GB minimum**, matching this
directory's convention of every service declaring an explicit `mem_limit`
(see `03-INFRA/deploy/README.md`'s Resource notes — an uncapped container
that OOMs can take the whole VPS down with it). The maintainer's own
instance runs with a 4 GB cap for headroom; either fits the free tier above
with room to spare for the other stacks (`vault-mcp` at 512 MB, Firecrawl at
~6 GB, OCR at 2 GB).

Deployment rules, consistent with every other stack in this directory:

- **Mount the vault read-only** (`/vault:ro`). This service only ever
  reads notes; it writes exclusively to its own index directory, on a
  separate volume.
- **Join the same Docker network as `vault-mcp`** so the two can reach each
  other by container/service name over Docker's own DNS — no host port
  needs to be published for that traffic.
- **Do not expose a host port to the public interface.** If you want to
  curl it from your workstation for testing, bind the debug port to
  `127.0.0.1` only, same as every other stack here, and reach it over the
  SSH tunnel pattern in `03-INFRA/deploy/README.md`.
- **Give it a `healthcheck:`** against `GET /health`, same convention as
  every other service in this directory.
- **`restart: unless-stopped`.**

## 8. Wiring it up

Once the container is up and reachable from `vault-mcp`'s network:

```bash
SEMANTIC_URL=http://<your-service-name>:8080
SEMANTIC_ENABLED=true
SEMANTIC_MAX_LIMIT=10
```

Set these wherever `vault-mcp`'s other environment is configured, restart
it, then re-run `agent-sync` so the CLIs pick up the now-enabled
`semantic_search` tool.

## 9. Verifying you actually rebuilt it, not something close

- `curl http://<host>:<port>/health` → `"status":"ok"`, `"reranker":true`.
- `curl -G '.../search' --data-urlencode 'q=<a term that appears only in one note's title>' --data-urlencode 'k=5'` →
  that note should be the top hit, purely on the title-boost signal, even if
  the term never appears in the body.
- `curl -G '.../search' --data-urlencode 'q=<a concept described without using its name anywhere>' --data-urlencode 'k=5'` →
  the right note should still surface, purely on the vector-search signal —
  this is the case a lexical-only search would miss.
- Call the `semantic_search` MCP tool end-to-end from an agent CLI and
  confirm the results carry `path`/`title`/`score`/`snippet`, not just raw
  text.
- `docker stats` on the container should sit near the measured footprint in
  §7, not drift upward unbounded — that would mean the reindex loop isn't
  actually skipping unchanged files.
