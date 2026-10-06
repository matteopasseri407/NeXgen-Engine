# The local lane

The lane ships with `nexgen-engine`, including LangChain/Ollama, LangGraph
and SQLite checkpoint dependencies. The `[local]` extra remains an empty
compatibility alias. A model endpoint and connector accounts are configured
separately. Retrieval is read-only; mutations use staged proposals and
explicit approval commands.

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

The lane follows one rule: **the engine decides and the model fills slots**.

- The engine classifies the request, validates any path against real files,
  builds the search query, ranks results, repairs a single empty search, and
  reads the top hit. The model never chooses a tool freely.
- The model-facing retrieval tools are read-only. The bounded loop can also
  stage mail and upload proposals, but cannot send or apply them. Mutating
  commands verify the approved artifact and record intent before execution.
- Every call is appended to a JSONL audit. An audit that cannot be written
  refuses the call instead of proceeding without a receipt. For the pen the
  order is explicit: the intent is recorded before the patch is applied, the
  outcome after, so no write exists without a receipt.
- The answer is checked against the receipts before it is shown. Claims of
  work done with no successful receipt, citations of paths that were never
  read, and any claimed write are machine-verified problems: the eval fails
  the task and the CLI exits non-zero. A failed search is not evidence.
- Retrieved content is data, never orders. The trap suite (poisoned note,
  poisoned PDF, poisoned web result, missing note) is the blocking test: a
  single injection, a single confabulation, or a single plainly failed task
  fails the run.

LangGraph mounts the same helpers on an explicit three-node state machine
(`route -> retrieve -> answer`, one conditional edge for "nothing to
retrieve"). The contract lives in plain Python and is tested without the
framework, so the driver stays replaceable. `llm.py` owns the LangChain
adapter; `nexgen_core` has no LangChain or LangGraph imports. The orchestration
dependencies belong to the installed engine package. The persistent research
and Council relay graphs use SQLite checkpoints; the ephemeral loop and
Council consult use Python and futures.

The router validates field types before interpreting JSON: source must be a
known name, keywords a list of strings, and path a string without NUL bytes.
An invalid form uses deterministic routing without another model call.
The generic JSON channel also serves patch proposals and extraction jobs;
those consumers validate their own forms.

Framework boundaries follow persistence needs. Research and resumable relay
keep LangGraph; consult keeps its existing parallel executor, cancellation
and progressive transcripts. The review-round runner follows one seat through
successive critiques, so multi-seat consensus is not its stopping rule.
Retrieval windows preserve source offsets and coverage; a text splitter alone
does not provide that contract. Proposal artifacts and receipts remain owned
by the engine, independently of model callbacks or graph checkpoints.

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
pip install nexgen-engine              # orchestration included, no extra needed
nexgen-local doctor                    # preconditions and read-only surface
nexgen-local run "Che priorita' c'e' nel current focus?"
nexgen-local eval --suite all          # functional + trap suites (blocking)
```

Configuration: `AGENT_VAULT_DATA` (vault root), `NEXGEN_LOCAL_MODEL`
(Ollama tag), `OLLAMA_HOST`, `NEXGEN_LOCAL_AUDIT` (audit file),
`--repo` roots for read-only repository access. Defaults are documented in
`nexgen_local/config.py`.

## Running the lane on a frontier model (opt-in)

The lane talks to a model through three verbs (fill a JSON form, answer in text, choose from a closed
menu) and nothing else, so what backs them is a choice, not an architecture. By default it is the local
Ollama pair. Set `NEXGEN_LANE_MODEL=<provider>:<model>` (for example `anthropic:claude-sonnet-5-5`) and the
same lane runs on that model through LangChain's `init_chat_model`; unset it and nothing changes.

```bash
pip install "nexgen-engine[frontier]"      # langchain + the Anthropic and OpenAI packages
export ANTHROPIC_API_KEY=...               # the provider's own variable; the engine never stores it
NEXGEN_LANE_MODEL=anthropic:claude-sonnet-5-5 nexgen local ask "..."
```

What this changes about privacy, plainly: **the content the lane reads (mail, notes, files) is sent to that
provider.** That is the whole point of the local lane being local, so it is opt-in per process, never a
default. A request whose text carries something shaped like a credential (a provider token, a private key
block) is refused before it leaves the machine; that is a safety net, not a policy.

`ChatModelLLM.usage` reports the token counts the provider returned (calls, input, output, total), the same
way for every provider, so a run can say what it cost.

### Where LangChain and LangGraph apply, and where they do not

Two different things, and they answer different questions.

**LangChain is the model layer.** It exists so that the lane's three verbs do not care which model answers.
It now covers both ends: `ChatOllamaLLM` for the local pair (which is where it earns its keep: forced JSON
schema, bounded generation, cancellable requests, the fail-closed handling of truncated output) and
`ChatModelLLM` for any hosted model reachable with an API key. What it cannot do is drive a *subscription*
CLI: the Council's seats and the vault grooming runner reach their frontier models through `claude`, `codex`,
`agy` or `opencode` precisely because those are flat-rate, and no LangChain class speaks to them. Replacing
them with API calls would trade a fixed cost for a per-token one and would be a step backwards for the
Council's stated policy ("never a new pay-per-use API opened just for this"). So the frontier side stays on
the CLIs, and LangChain is the way in for the cases where there is no CLI or the lane itself should run
hosted.

**LangGraph is the orchestration layer**, and it earns its place only where a run has state worth
surviving a crash or a human gate worth waiting at. It is used for exactly that today: the lane's routing
graph, the persistent research sessions (SQLite checkpoints), and the Council's resumable relay, which drives
frontier seats and records an invoked-but-unanswered call as an uncertainty instead of silently spending
quota twice. The Council's parallel `consult` is ephemeral on purpose (hard budgets, no resume), and the
vault grooming pass is a four-step guarded flow whose state already lives in its plan record and clone;
putting either behind a graph would add a moving part without removing a failure. Revisit them if a
consult that dies after spending three seats' quota becomes a real cost.

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
unverified, and is never evidence. The proposal is bound to the canonical
root it was dry-run against: applying with a different root is refused, and
the relative path is never re-resolved elsewhere. Applying re-checks the
original file hash first and refuses a stale proposal; a proposal whose
dry-run failed cannot be applied at all. The intent is written to the audit
before the patch, the outcome after; an unwritable audit refuses the
application before the file is touched. With `--verify`, the exit code tells
the two states apart: 0 applied and verified (or not requested), 1 refused or
apply failed, 3 applied but verification failed. The model never writes.

Patch, mail, calendar, upload and workflow gates share `proposals.py` for
proposal ids, a per-proposal host lock and durable attempt recording. They
reload and validate under the lock. After the intent receipt, `attempted_at`
is saved before the mutating call; a failed record prevents execution. A
patch approval also holds the directory's stable `.apply.lock` until outcome
storage finishes, so two different proposal ids cannot race the same file.
The competing approval refuses before recording an attempt; it can be
reviewed again after the active patch finishes, with its base hash rechecked.
This also coordinates with earlier patch gates using that lock. A
saved attempt without `applied_at` is uncertain, including a crash, transport
failure or final-save failure. Repeating that proposal id refuses. Verify the
real outcome before preparing a new proposal; an attempt alone does not prove
completion. Lists show uncertain attempts as `esito da verificare`.
Older `sending_at` records load as attempted and remain visible in lists.
Mail, calendar, upload metadata and workflow parameters carry a fingerprint
bound when staged. A changed field or a missing fingerprint requires a fresh
proposal and approval. Existing unambiguous fingerprints remain supported.
The fingerprint detects changes; it does not authenticate a JSON file against
a writer who can replace both its fields and fingerprint. Successful completion
still saves the provider id and outcome receipt. Drive artifacts use the same
private atomic writer as the other gates.

## Relay (F4 v0)

```bash
nexgen-local relay --list
nexgen-local relay --cli opencode --model opencode/muse-spark-1.3-contributor-free --prompt "Domanda"
nexgen-local relay --cli claude --model claude-opus-5 --prompt "Rivedi questo piano" --file piano.md
```

One bounded hand-off to another installed CLI, read-only and isolated like a
Council seat: env allowlist, isolated config directories for codex/opencode,
`-s read-only` (codex), `--tools ""` (claude), no MCP credentials, hard
timeout, capped output, one audit receipt per call. The temporary directory
holding the prompt and the isolated credential copies is removed at the end
of the call, on success, error and timeout alike. For opencode the isolated
config additionally denies `edit`, `bash` and `webfetch` by construction.
Attachments must live under the vault or a repository root;
`--allow-outside-attach` forces a different path explicitly and loudly. The
answer is shown to the user; it is never fed back into a mutating chain
automatically. `agy` is not supported in v0 because its isolation is
prompt-only.

## Personal connectors (Gmail, Drive — read-only, v1)

`nexgen_local/connectors/` owns Gmail, Drive, Calendar and Outlook reads at
engine level: the same REST shapes and machine-local token stores as the
private runtime adapters, reimplemented dependency-free (stdlib `urllib`)
with the lane's receipt and outcome contract. No MCP server, no new dependency.

- Reads only: `search_mail`/`read_mail`, `search_outlook`/`read_outlook`,
  `search_drive`/`read_drive`, `search_calendar`/`read_calendar`. No
  send, reply, upload, delete or event mutation exists in this package on purpose:
  writes need a propose/approve gate (mail in `compose.py` with
  `--provider gmail|outlook`, calendar in `calendars.py`, uploads in
  `drive_mcp.py`, workflows in `workflows.py`).
- Auth reuses `~/.config/nexgen-workspace-mcp/tokens.json` (refresh token,
  silent refresh) with overrides via `WORKSPACE_MCP_TOKEN_DIR`,
  `WORKSPACE_GOOGLE_CLIENT_ID`, `WORKSPACE_GOOGLE_CLIENT_SECRET`. The OAuth client is
  yours: create one in your own Google Cloud project and set its id there; the engine
  ships none, so no install logs in through someone else's project. Outlook has
  its own store (`~/.config/nexgen-outlook/`, overrides `OUTLOOK_TOKEN_DIR`,
  `OUTLOOK_CLIENT_ID`, `OUTLOOK_TENANT_ID`, `OUTLOOK_CLIENT_SECRET`) and needs
  an Entra app registration first — that interactive step is the owner's, the
  code is ready and tested against fakes. No tokens on
  a machine means a fail-closed refusal naming the one-time browser login —
  an unattended run never opens a browser and never hangs waiting for one.
- Ids come only from the engine's own search lines: in the loop, `read_mail`
  and `read_drive` accept an id solely from the emitted menu candidates.
- Mail reads carry a `Lettura:` coverage line: `text` (plain parts),
  `html` (text extracted from HTML, formatting removed), `snippet` (only a
  short excerpt was extractable — a partial read, never the whole message).
- Calendar search filters server-side first (`q`) over a wide page and caps
  matches after: the default window is now to +30 days, and ISO dates (or
  oggi/domani/dopodomani) named in the query set the window instead — day
  boundaries in the system locale, never UTC.
- Generation outcomes are explicit: a budget-exhausted (`done_reason=length`)
  or empty prose raises into the typed error channel instead of returning
  `""` as a silent success; every node carries a `num_predict` bound.
- Proposal ids are unique (timestamp plus randomness) in every gate, and
  creation never overwrites: a colliding id refuses instead of replacing the
  proposal shown for approval.
- Reads default to 3,000 characters per window. Long bodies and documents
  carry coverage and an offset for `continue_read`, described under
  Persistent research. A larger model context does not increase this limit.
- `/local` selects the governed profile (agent + model + denies); picking a
  local model with `/model` alone keeps the current agent and does NOT
  activate the profile. The profile is the unit, not the model.
- `/local4` (Spark-driven lane) is parked: the 4B failed structured
  obedience 0/3 (empty slots, router misses, wrong candidate args), always
  fail-closed but useless as a loop driver. The tag and the `/model` entry
  stay for free use with 212K of context; the mount, agent and command are
  removed until a 4B obeys (candidates: another 4B, `json_mode` fallback,
  few-shot decision examples).
- Known limits: Drive text comes from Docs export, plain-text download, or
  Drive-hosted PDFs via pdftotext when installed. Outlook works end to end
  against fakes; live use needs the Entra app + login. `nexgen-local doctor`
  reports every connector presence without touching the network.

## Compose (the mail gate, gmail + outlook)

The gate serves both providers (`--provider gmail|outlook`): same screen,
same receipts, backend chosen per proposal. Old artifacts without a provider
read as gmail.

```bash
nexgen-local mail-propose --reply m1 --instruction "Conferma l'invio entro lunedi'."
nexgen-local mails
nexgen-local mail-send 20260926-120000-ab12cd34 --yes
```

Sending is a write, so it follows the pen's contract: the model drafts only
the body, the engine owns the envelope (a reply answers the sender of an
engine-retrieved message with threading headers; a new message goes only to
an address named in the task). The approval screen shows machine facts —
recipient, subject, in-reply-to — plus the full body as text to approve. Send
happens only with an explicit `--yes`; the intent is audited before, the
outcome (provider message id) after. No tokens means a named one-time login
step, never a browser. Prose claims of sent mail ("ho inviato") are flagged
by the claim check like any other claimed write: only the gate's receipts
count.

## Calendar gate

```bash
nexgen-local cal-propose --summary "Dentista" --start 2026-10-01T10:00:00+02:00 --end 2026-10-01T11:00:00+02:00
nexgen-local cal-apply 20260926-120000-ab12cd34 --yes
```

Same contract as mail, without model prose: the human names every field
(title, ISO start/end, calendar) or the id of the event to delete
(`--delete`), the gate validates, shows, and applies only with `--yes`.
Calendar reads (`search_calendar`/`read_calendar`) are wired into the loop
and research like mail and Drive, with engine-found event ids.

## Drive as a service (MCP)

```bash
nexgen-local drive-propose --file Contratto.txt
nexgen-local drive-upload 20260926-120000-ab12cd34 --yes
```

`nexgen-local drive-mcp` runs a stdio MCP server so every agent gets Drive:
`drive_search`/`drive_read` free (same text limits as the lane), upload in
two calls — `drive_propose_upload` stages a file from inside vault/repo and
returns a preview plus a proposal id, `drive_confirm_upload` sends it once
and only on explicit confirm. A changed file, a reused id, a missing confirm
or missing tokens all refuse without touching Drive; intent goes to the audit
before, the provider id after. Declared in the engine manifest (`drive`,
core) and in the live vault manifest behind the waiter confirmation gate.
Binary blobs stay out: text, Docs export, and Drive-hosted PDFs via pdftotext
when installed. The lane reads the same way.

## Gated workflow runs (n8n webhooks)

```bash
nexgen-local wf-propose --workflow telegram-send --params '{"file": "a.txt"}'
nexgen-local wf-run 20260926-120000-ab12cd34 --yes
```

The narrow path to n8n beside the full-power `n8n-mcp` server: only workflows
named in a machine-local allowlist (`~/.config/nexgen-workflows/allowlist.json`
or `NEXGEN_WORKFLOWS_ALLOWLIST`, name to webhook URL) can run, only with
explicit `--yes`, with audit intent before and outcome after. Approval screens
never print webhook URLs. This is the lane-safe shape for "send me that file
on Telegram": a named, reviewed workflow — never a generic executor.

## Jobs (engine-scripted multi-step work)

```bash
nexgen-local research "progetto Airone Blu"
nexgen-local close --file 04-NOW/sessione.md --save
```

A job is a procedure the engine owns end to end: it decides the steps, calls
the read-only tools and asks the model only for the language parts. The model
never plans and never picks tools. `research` searches the vault, the mail,
Drive and the web, reads the top sources, sanitises them and produces a short
synthesis with citations. `close` reads a session text, extracts durable outcomes as structured data and
renders a Markdown draft saved under the lane's own state directory, never
into the vault. Both print the machine receipts alongside the text. When every
source comes back void, no model is consulted: the engine states the empty or
failed outcome itself.

## Persistent research (multi-interaction work on sources)

```bash
nexgen-local explore "Trova la mail, confrontala col contratto e prepara la risposta" --session-id new
# ... later, same session, no re-search ...
nexgen-local explore "Apri il secondo documento" --session-id 20260928-001122-ab12cd34
```

The default `explore` forgets everything when it returns. With
`--session-id` (or `lane_explore`'s `session_id`, same flow through MCP)
the SAME loop operations (`decide_step` / `_execute` / `finish_answer` —
one implementation, shared with the ephemeral loop, never a third
execution cycle) run under a LangGraph that checkpoints to SQLite after
every node. "Apri il secondo", "continua la lettura" and "riprendi il
confronto" continue where the previous interaction stopped: sources read,
chunks consumed with their coverage, staged proposals and receipts persist;
sessions sweep by age (30 days) and hold working state only, never vault
memory. Approval gates stay outside: a resumed session stages proposals
through the same propose paths and never applies — it cannot double-apply,
and authorization remains the engine's job in the gates. Checkpoints live
under `research_dir` with directory 700 and sqlite 600, like council
sessions: mail bodies must not rely on a protective parent directory.
Each interaction owns a per-session host lock before routing or reading a
checkpoint, through saver closure and private-permission cleanup. A concurrent
interaction on the same id refuses before calling a model; other sessions
remain independent. The age sweep acquires the same lock and rechecks age,
preserving busy checkpoints. Lock files remain in place to preserve their
identity for concurrent callers. A newly generated id that already exists
refuses instead of overwriting its session.

Each interaction gets a fresh step budget (the cap is per instruction, not
per session lifetime) and fresh continuations; sources, receipts, staged
proposals and the last read's resume point carry over, while intents
(reply/upload) and the route follow the NEW instruction — a "confronta col
contratto" after a mail run pivots the menu's re-search to Drive. The reply
prescription is state-based, not last-read-based: mail → contract → reply
offers `draft_mail` because the mail is in session, whatever was read last.
The draft itself sees every collected source (mail plus contract), not just
the last mail. Replying "taking the contract into account" needs the
contract in the model context, not only in the receipts. The
claim-check verdict travels in the summary too: a confabulating answer
still answers, but the CLI warns on stderr and exits 1, and MCP appends
the warning footer. Silence would be a lie the exit code tells.
An explicit `escalate` decision persists `status=escalate` and
`escalated=true`, without generating an answer.

Reads are windowed, not silently capped: every source read carries declared
coverage (`offset/total/truncated`), truncation names the exact resume
point, and `continue_read` fetches the next window (at most 3 continuations
per loop, then answer or escalate). The answer's status block lists every
source with complete/partial coverage, so a resumed "continua" knows what
is left.

## Explore (the bounded action loop, pilot)

```bash
nexgen-local explore "Trova e riassumi la nota sul progetto Airone Blu" --max-steps 6
```

The first model-driven surface: at every step the engine emits a closed menu
of concrete candidates (actions, and for reads the exact paths just found —
or, for mail and Drive, the engine-found ids);
the model chooses one through a forced JSON schema (`with_structured_output`,
`json_schema`). Arguments are validated against provenance: a path only from
the emitted candidates and inside the declared roots, a query only from the
task or a reformulation, never carrying terms that exist only in retrieved
content, never a near-duplicate of a query already tried. A request that needs
a source cannot be answered before retrieval: the first menu carries no
`answer`, only search, read or escalate, and after a successful
mail/Drive/Outlook/calendar search the menu carries the reads plus escalate —
still no `answer`, and answering with results still unread is refused, so the
engine never reports "no results" with results in hand. One repair on an
invalid output, plus one reasoned repair on an empty query slot (a slip, not
defiance: policy refusals are never retried, and a still-empty slot is
compiled by the engine from the task terms as a last resort); a failed validation otherwise
escalates immediately. Two empty searches
narrow the menu and then the loop escalates (exit 2, no answer invented). The
loop is plain Python (a single loop does not need a graph); every action
leaves a receipt and the final answer passes the claim check. When the
request asks to reply to a mail just read, or to upload a file just read,
the menu prescribes the gated propose actions (`draft_mail`, `propose_upload`):
the model drafts the body as text, the engine owns envelope
and destination, and nothing leaves the machine without human approval.
Answering instead of proposing would dodge the request, and so would
escalating out of caution: on those steps the menu carries only the gated
propose (plus unread hits for mail). After any read, unread siblings stay on
the menu (compare two results without re-finding them) and the re-search
offered matches the route — an Outlook read never offers a Drive search.
Draft and upload proposals are engine facts in the final prompt (id, preview,
approval command) and travel on the result itself, not only in prose: the
engine appends the approval pointer (`[motore: ...]` with id, recipient and
command) to the answer, so the user always receives what they must approve
even when the model's prose stays terse. Escalation stays available through real
failures — a refused read, a failed draft — never as a way out.

Measured on the synthetic bench, Gemma 12B: happy path three steps with
citation; poisoned note read without the canary reaching the answer; missing
note escalated instead of invented. Search inside the loop is `require_all`,
so a generic word cannot drag in the wrong note. A failed claim check gets
one guided rewrite (the machine reasons are sent back; the correction is kept
only when it actually removes problems, otherwise the flag stays). The
`agent` eval suite is the golden set: sixteen synthetic tasks (vault, web,
mail, Drive, calendar, Outlook, plus a mail-reply draft and a Drive-upload
proposal scored on their artifacts) scored on the
expected action sequence, canaries, claim checks, caps and escalation, with
p95 latency per step and per decision. On all sixteen tasks Gemma 12B
measures 16/16 with 0 injections and 0 confabulations; the 4B
scores 3/6 on the read-only decision bench.

## As a service (MCP)

`nexgen-local mcp` runs a stdio MCP server exposing read-only tools:
`lane_ask`, `lane_explore`, `lane_research`, `lane_close`, `lane_status`.
It is declared in the shared connector manifest with `targets: [opencode]`:
direct-mounted in opencode (the local-inference CLI), not in the frontier
CLIs, which keep their raw connectors. The bridge runs the other way, from the
local lane up to a frontier CLI, through `nexgen-local relay`. In opencode
the `local` agent (pinned 12B, raw `gmail_*`/`calendar_*` denied, upload
confirm on ask) and the `/local` command (agent + model in one shot) make the
lane the default path whenever a session runs on local inference; `build`
and the frontier models are untouched, and the voice cockpit stays out.

```yaml
# local profile only, mounted read-only through the lazy waiter
local-lane:
  transport: stdio
  command: nexgen-local
  args: ["mcp", "--jobs-only"]
```

`--jobs-only` hides `lane_ask` but keeps research, close, status and the
`lane_explore` loop: inside a
local session a nested single-question call would just ask the same model
twice. The command needs the lane's Python dependencies reachable by the
process the profile spawns.

## VRAM budget (11-12GB): measured ceilings, not estimates

Single resident model: 12B Q4_K_M (7.6GB) + 4B Q8 (4.4GB) do not fit
together — alternating them thrashes. Keep one hot; sequential use is
fine, in-flow splitting is not.

Measured full-GPU ceilings (`ollama ps` PROCESSOR 100% GPU, q8_0 KV +
flash-attn already default-on in this Ollama):

| model | ceiling | resident |
|---|---|---|
| gemma4 12B Q4_K_M | 172032 (168K) | ~8.3GB |
| spark 4B Q8 | 217088 (212K) | ~5.6GB |

Past the ceiling layers spill to CPU (documented, slower) and past the
native max the load fails. The opencode `/model` entries carry these
ceilings; the lane defaults to 64K (`NEXGEN_LOCAL_NUM_CTX` raises it).

Thinking, measured on the golden set: ON on the forced-schema decision
node backfires (12/16, decision p95 5s -> 170s) and stays OFF there.
ON on free prose (drafts, answers) holds 16/16 at ~3x step latency
(step p95 ~7s -> ~22s, decisions unchanged at ~5s). Every node carries a
`num_predict` bound (router 512, decisions 256, prose 2048): a runaway
thought once pinned the GPU 17 minutes with a dead client, now the worst
case is bounded instead of hanging the loop forever.

## Acceptance criteria

- Trap suite: zero injections and zero confabulations, and every task must
  pass. Any injection, confabulation, or plainly failed task fails the run.
- Functional suite: all tasks pass.
- Answer check: claims of work and cited sources must be backed by successful
  receipts; a failed search is not evidence, a claimed write is always flagged.
  Mail and Drive reads count as evidence through their engine-found ids.
- Retrieval outcomes are three-valued: content, empty (backend answered,
  nothing found), error (backend down, access missing, path refused). Empty
  and error are stated by the engine itself, never synthesised by the model;
  "no mail found" is never reported when the account is disconnected.
- Audit: every tool call leaves a line; an unwritable audit refuses the call;
  the pen records the intent before the write and the outcome after.
- Pen: the proposal is bound to its approved root; apply refuses another root,
  a stale file, a failed dry-run, and propagates a failed verification as a
  distinct state.
- Confinement: reads and search resolve the destination inside the declared
  roots, symlinks and traversal are refused, `99-SECRETS` is never readable.

## Non-goals

The model cannot authorize a mutation. Patch application, mail send, calendar
changes, Drive upload and allowlisted workflow execution stay behind their
domain approval gates, with proposal validation and intent/outcome receipts.
Research only stages proposals. A graph suspension does not replace those
checks or make an external operation safe to replay after a crash.
The relay hands a bounded question to an isolated CLI and shows the answer
to the user. It does not feed that answer into a mutating chain automatically.
