# Daru burns a Claude window in two minutes: where the tokens go

A read-only forensic analysis, written 2026-11-14.
Nothing here is implemented, no configuration was touched, and nothing was committed.
Every figure names where it came from: a file and line in this tree, a page of
`docs.openclaw.ai`, or a transcript on a machine.
Where a number is an estimate it says so, and where it was not measured on the VPS it says that too.

Scope: agent `capo` (Daru) on the Oracle VPS, model `anthropic/claude-opus-5-5` through
`agentRuntime: claude-cli`, after the four quota commits of 2026-11-14
(`9fd611ea`, `179c21df`, `0559b598`, `69bc7435`).
Pinned OpenClaw: `2026.11.2` (`03-INFRA/agent-universal-layer/openclaw/pin.json`).

How to read the citations:

- a Python file and line, such as `profile.py:359-366` or `openclaw/plan.py:300`, is relative to
  `03-INFRA/scripts/nexgen_core/`;
- a bare `.mjs` file and line, such as `cli-backend-BBPDHJCu.mjs:106`, is a file inside the pinned
  OpenClaw install, `node_modules/openclaw/dist/`, unless it carries a `dist/` prefix;
- `squad.yaml`, `openclaw-vps-ottimizzazione.md` and `claude-cli-quota-burn-2026-10-27.md` live in the
  KnowledgeVault under `03-INFRA/` and `04-NOW/`, not in this repository;
- `docs.openclaw.ai/...` is the upstream documentation of the pinned release.

## 0. The short version

1. **Fable's connector deny does apply to the `claude-cli` path, and it is the reason today's per-call
   cost is 78k instead of 943k.** A connector whose every tool is denied is not merely filtered: OpenClaw
   **removes it from the MCP config it hands Claude Code**
   (`bundle-mcp-codex-B3JMdu8Z.mjs:74`, `if (!prepared || prepared.allowedTools.length === 0) return []`).
   Measured on the live gateway: 945,389 tokens for one request without the deny, 77,840 with it.
2. **The residual floor is OpenClaw's own tool catalogue**, which
   reaches Claude Code through the loopback MCP server marked `alwaysLoad: true`, plus Claude Code's
   own prompt and skill catalogue. Measured on a local capo request: **78,717 tokens**, of which the
   51 `mcp__openclaw__*` schemas are 112,239 of about 264,000 prompt characters, **42%**.
3. **That floor is paid on every model call, and a Daru turn was ~74 model calls.** 74 x 78k = 5.8 M
   cache-read tokens per turn. The 2026-10-27 turn was 65.9 M because the seed was 943k. Today the seed
   is 78k; if the call count is the same, the turn is still millions of tokens, and today's exact number
   was not recorded anywhere I can read.
4. **The engine's new ceiling cannot see this.** `context_budget.py:20-22` reads *the first prompt* and
   fails above 150,000. A session that passes at 77,840 and then makes 74 calls spends 5.8 M and the
   check is green.
5. **What config can do cheaply:** shrink the loopback catalogue with an allowlist written in
   `tools.byProvider["anthropic/claude-opus-5-5"]` (-15k tokens per call, -19% on the turn), cap the
   per-call context with `contextTokens: 200000` (ceiling 1M -> 200k), and drop the effort from high to
   medium (output side). What config **cannot** do, and I did not do: bound the number of calls per turn,
   make the loopback server stop being `alwaysLoad`, or let Claude Code read its own MCP file. All three
   are named in section 6 as L6, with the reason.

## 1. The path a Daru turn takes

```
Telegram / panel
  -> OpenClaw gateway turn (agent capo)
     -> claude-cli backend (bundled anthropic plugin)
        -> warm Claude Code subprocess:
             --strict-mcp-config --mcp-config <temp mcp.json>
             --model claude-opus-5-5  --effort high
             (OpenClaw's system prompt is sent on every turn, not only the first:
              systemPromptWhen: "always")
           temp mcp.json contains: openclaw (loopback, alwaysLoad: true), lazy-mcp, and any
           connector whose tool policy still allows at least one tool
        -> each tool call returns to the gateway over the loopback
     -> usage per model call recorded in the Claude Code JSONL transcript
```

The three facts that decide the bill:

- `createMcpServerConfig` in `dist/mcp-http.loopback-runtime-DxTYlVrU.mjs:199-206` writes
  `alwaysLoad: true` on the `openclaw` server. That flag is not configurable.
- `dist/prompt-context-Nx1uhVGB.mjs:119-125` always injects
  `--strict-mcp-config --mcp-config <temp file>`, so Claude Code never reads `~/.claude.json`
  for this agent. `cli-backend-BBPDHJCu.mjs:158` keeps the system prompt on every turn
  (`systemPromptWhen: "always"`), which is why its size is paid per call and not per conversation.
- `dist/cli-backend-BBPDHJCu.mjs:10-22` sets the launch args, including
  `--allowedTools mcp__openclaw__*`, and blocks Claude's native `Bash` because OpenClaw owns `exec`
  (`cli-shared-CfIR-juz.mjs:318-322`). Every command is therefore a model call plus a tool call.

## 2. Question 1: which MCP servers and tool schemas reach Claude Code for capo today

### 2.1 What the engine writes

| Writer | What it writes | Called by |
|---|---|---|
| `openclaw/claude_loopback.py:67-107` | `agents.entries.<id>.tools.deny = ["<server>__*", ...]` for every agent detected on `claude-cli` | `loopback_guard.apply_loopback_guard` |
| `openclaw/loopback_provider_policy.py:166-218` | `tools.byProvider["<provider or provider/model>"].deny`, same patterns | same |
| `openclaw/loopback_guard.py:40-71` | one owner for both, so a newly mounted connector is born denied | `mcp_render/openclaw.render` (sync) and `openclaw/plan` (setup) |

`claude_cli.is_claude_cli` (`claude_cli.py:136-142`) decides who is affected, with OpenClaw's own
precedence: the agent's `models` entry, then `agents.defaults.models`, then the provider model entry,
then the wildcard, then the provider block. For capo the pin is
`agents.defaults.models["anthropic/claude-opus-5-5"].agentRuntime.id = "claude-cli"`
(`profile.py:359-366`), and `loopback_provider_policy._scan_models_maps` turns that into the
`byProvider` key `anthropic/claude-opus-5-5`.

The local Windows install shows the shape the VPS should have (keys only, no values):

```
agents.entries.capo.tools.deny =
  drive__* firecrawl__* n8n-mcp__* outlook__* playwright__* supabase__*
  vault-library__* vault-ocr__* vercel__* workspace-mcp__*
tools.byProvider["anthropic/claude-opus-5-5"].deny = same ten
```

### 2.2 Does that deny reach Claude Code, or only the embedded runtime?

It reaches Claude Code, and by removal rather than by a deny list.
For an ordinary managed turn OpenClaw builds a per-run MCP policy and projects it onto the CLI config:

- `bundle-mcp-codex-B3JMdu8Z.mjs:40-68` classifies each connector tool as allowed or denied for this
  run, using the same policy the embedded runtime uses.
- `:71-84` then **drops a server whose allowed list is empty** from the CLI's `mcpServers`, and
  narrows a partially denied one with `toolFilter.include/exclude`.
- `prompt-context-Nx1uhVGB.mjs:289-335` writes the result to the temporary `mcp.json`, and
  `:119-125` adds the still-denied tool names to `--disallowedTools`.

So for capo today, all ten connectors are absent from Claude Code entirely. They are not lost: they
remain mounted in `mcp.servers`, and the agent reaches them through the engine's `lazy-mcp` gateway,
which `claude_loopback.NOT_A_CONNECTOR` deliberately keeps out of the deny
(`claude_loopback.py:43`), and which OpenClaw defers behind Claude's native `ToolSearch`
(`docs.openclaw.ai/tools/tool-search`: "Claude CLI runs use Claude's native ToolSearch for deferred MCP
definitions").

Conclusion for Q1: Fable's change is what stands between Daru and 943k per call. It is applied on the
`claude-cli` path, which is the only path where it matters, and the measurement proves the effect.

### 2.3 What still reaches Claude Code, and how big

Measured on this machine from capo's own Claude Code transcript,
`~/.claude/projects/C--Users-<user>--openclaw-workspace-capo/<session>.jsonl`
(2026-10-23, Opus 5.5, `claude-cli`, the same loopback mechanism):

| What | Count | Size |
|---|---|---|
| tools in the API request | 64 | 135,775 |
| of which `mcp__openclaw__*` (loopback, eager) | **51** | **112,239** |
| of which Claude Code's own + `ToolSearch` + placeholder | 13 | 23,537 |
| system prompt blocks | 3 | 25,821 |
| `skill_listing` attachment | 1 | 66,623 |
| `agent_listing_delta` attachment | 1 | 16,389 |
| `deferred_tools_delta` (connector names only) | 1 | 9,983 |
| `mcp_instructions_delta` (lazy-mcp, vault-library) | 1 | 2,842 |
| memory / session context | 2 | 6,227 |
| **first assistant call** | 1 | **cacheWrite = 78,717 tokens** |

The 51 eager tools are OpenClaw's own surface: `exec`, `process`, `read`, `write`, `edit`, `browser`,
`sessions*`, `nodes`, `canvas`, `terminal`, `plugins`, `gateway`, `secrets`, `message`, `tts`,
`talk_voice`, `dir_fetch`, `file_fetch`, `file_write`, `mobile_ui`, `presence`, `intent`,
`node_inference`, the goal tools and so on. None of it comes from the manifest: it is the gateway's own
toolset, and it is eager because of `alwaysLoad`.

The VPS measured 77,840 tokens after the deny, within 1% of this 78,717. That is the strongest available
evidence that the VPS prompt has the same composition, and it is why the breakdown below can be used for
the VPS with the caveat that today's exact bytes were not read.

## 3. Question 2: what OpenClaw injects besides the tools, and how big

Yes, it injects its own context, and on this path it is the second largest term.

| Source | Where it comes from | Budget on 2026.11.2 | Measured on the local capo turn |
|---|---|---|---|
| OpenClaw system prompt: tooling, skills list, workspace, time, runtime, self-update | rebuilt every run (`docs.openclaw.ai/reference/token-use`) | skills list capped by `skills.limits.maxSkillsPromptChars` (the documented example value is 18,000 chars) | inside the 25,821 chars of system blocks |
| Workspace bootstrap files `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `BOOTSTRAP.md`, `MEMORY.md` | injected as Project Context | 20,000 chars per file, 60,000 total (`agents.defaults.bootstrapMaxChars`, `bootstrapTotalMaxChars`) | the squad's composed files are small; `docs/proposals/openclaw-quota-burn.md:198` measured 1,896 bytes for `main` |
| Channel history | not replayed on a resumed native session; only a bounded note about rows the CLI missed (`docs.openclaw.ai/gateway/cli-backends`) | newest 8 records, 2,000 chars each | none in this turn |
| Skills **catalogue** as Claude Code sees it | Claude Code reads `~/.claude/skills` because the backend forces `--setting-sources user` (`cli-shared-CfIR-juz.mjs:143`) | none | **66,623 chars, about 20,000 tokens** |
| Claude Code agents catalogue | `~/.claude/agents` | none | 16,389 chars, about 5,000 tokens |
| Per-turn runtime context (time, node, delivery guidance) | `prompt-context-Nx1uhVGB.mjs:499-530` | small | small |

Two consequences worth stating plainly:

- The skill catalogue is the one expensive item that **belongs to Claude Code, not to OpenClaw**.
  `agents.entries.capo.skills: []` is a first-class key and removes OpenClaw's own skills list, but it
  cannot remove Claude Code's `skill_listing` while `--setting-sources user` is forced. Whether
  2026.11.2 still loads it for managed turns is exactly the kind of thing the docs assert ("managed
  turns load no Claude skills") and the code does not obviously enforce: on 2026-10-23 it was loaded,
  on the same machine, for this agent. **Unverified for 2026.11.2 on the VPS; command in section 8.**
- The workspace bootstrap files are not the problem here. They are capped and the squad's are small.
  The 78k is tools and catalogues.

## 4. Question 3: why 74 calls in one turn

### 4.1 It is the tool loop, not sub-agents, retries, the heartbeat or polling

- **Tool loop.** Claude Code emits one `assistant` record per model call, and every tool result starts
  another call. 74 calls in a 5m38s turn is roughly 70 tool calls. The local transcript above is the
  smallest possible turn for comparison: one prompt, one answer, no tool use, and it still cost a
  single call of 78,717 tokens. That is what the seed alone buys.
- **Sub-agents: off by default.** `managedNativeTools` (`cli-backend-BBPDHJCu.mjs:36-45`) offers
  Claude's `Agent`/`Workflow` only when the operator lists them under
  `plugins.entries.anthropic.config.managedNativeTools`; the default is `["WebSearch"]`. The local
  `openclaw.json` has no such key. On 2026.11.2 this is not a source of extra calls unless somebody
  opted in.
- **Retries.** A provider retry appears in the transcript as a repeated request after an error, and
  OpenClaw's fallback chain is a *different* provider. Nothing in the recorded numbers points at it.
- **Heartbeat.** It is an automation job on this release, and `setup_cron.heartbeat_job_step`
  (`openclaw/setup_cron.py:99-136`) switches it off unless `squad.yaml` asks for it. `squad.yaml` has no
  `heartbeat` block.
- **Supervisor polling.** The supervision adapters poll the CLI every 2 s
  (`nexgen_local/supervision_adapters/*.py:34`, `POLL_S = 2.0`) and polling is a local status read, not
  a model call. `nexgen_sup_*` calls are ordinary tool calls inside the loop, one model call each.

### 4.2 Does OpenClaw re-send the whole context, or does Claude Code keep its own session?

Both, and the distinction is the whole cost model.

- **Across turns**: OpenClaw keeps the native session. The backend declares
  `sessionArgs: ["--session-id", "{sessionId}"]`, `resumeArgs: [..., "--resume", "{sessionId}"]`,
  `sessionMode: "always"` and `liveSession: "claude-stdio"` (`cli-backend-BBPDHJCu.mjs:130-161`), and keeps
  one warm Claude Code subprocess for compatible turns. A follow-up turn sends the new prompt plus the
  system prompt, not the history: Claude Code owns the history.
- **Inside a turn**: every tool call re-sends the accumulated context, because that is how a model call
  works. On 2026-10-27 the average per call was 892k against a 943k first call, so the growth from tool
  results was negligible next to the seed. Today the seed is 78k, so tool results matter more, but the
  arithmetic is the same: `calls x seed`.
- **The expensive exception**: a native session that has to be rebuilt. The backend sets
  `reseedFromRawTranscriptWhenUncompacted: true` (`:153`), so a fresh native session receives the saved
  OpenClaw history once, and `docs.openclaw.ai/gateway/cli-backends` says it plainly: "that first turn
  pays a full prompt-cache write".
  Any change to the tool surface, the system prompt, the model or the thinking level resets the native
  session. **A config edit during the day is paid for by the next Daru turn, at full history size.**
  Today's setup run (`nexgen openclaw setup --server`, per the Vault) rewrote the deny, and a deny
  change is a tool-surface change: that is exactly the event that forces the re-seed.

### 4.3 The arithmetic

| Day | Seed per call | Calls | Cache read per turn | Source |
|---|---|---|---|---|
| 2026-10-27 | 943,000 (first call cacheWrite 942,960) | 74 | 65,981,727 | Vault note, `openclaw-vps-ottimizzazione.md:188-200` |
| 2026-11-14, deny in place | 77,840 measured, 78,000 used below | 74 (assumed unchanged) | **5.77 M (estimate)** | first prompt measured; call count is the 10-27 figure and today's was not recorded |

Today's real numbers were not captured: nothing in the Vault, nothing in this tree. The calculation
above is the shape of the turn, not a measurement of today's turn. Section 8 has the one command that
replaces it with the real figure.

## 5. Question 4: is Opus used where Sonnet would do

The evidence says yes for a large part of the loop, and the owner's own definition of the role says so
too: Daru "decides, translates, supervises, does not execute"
(`squad.yaml:52`). A turn of ~70 tool calls is execution, and each of those calls runs at
Opus prices with `--effort high`.

- `thinkingDefault: "high"` (`squad.yaml:54`) becomes `--effort high` for Opus 5.5, because the model
  requires adaptive thinking and ignores `MAX_THINKING_TOKENS`
  (`cli-shared-CfIR-juz.mjs:51-80`, `anthropic-B3HirdzP.mjs`, `requiresClaudeMandatoryAdaptiveThinking`).
  The local transcript's request confirms the mechanism with `medium`: `output_config: {"effort":"medium"}`,
  `thinking: {"type":"adaptive"}`, `max_tokens: 128000`.
- Thinking tokens are billed as output, at the dearest rate, on every one of the calls.
- The capabilities that make Daru Daru are the supervisor tools (`nexgen_sup_*`, written by
  `tools_profile.allow_supervisor_tools`), not the model tier. A decision turn needs Opus; the sixty
  calls that fetch, read, patch and re-check do not.

I am not proposing to move Daru off Opus: "Opus solo a Daru" is the owner's rule
(`openclaw-vps-ottimizzazione.md:178`) and it is not mine to change. What the numbers support is a
cheaper effort on the mechanical calls, and a second, cheaper agent for the execution half of a
supervised job, which the engine's own supervision already models.

## 6. Question 5: the minimal config-only changes, with numbers

L1 to L5 are upstream, first-class settings of the pinned release (L4 is a discipline rather than a key),
and none of them removes a capability Daru uses. "Seed" means the tokens in every model call: the prompt
before any tool result. L6 is the lever that would matter most and is not available; it is described, not
done.

### L1. Shrink the loopback catalogue for the Opus model (biggest lever)

Why it works: the 51 eager schemas are 42% of the prompt, and the tool policy decides what the loopback
publishes. `tools.byProvider` accepts `profile`, `allow`, `alsoAllow` and `deny`
(`zod-schema.agent-runtime-B7VQsP09.mjs`, `ToolPolicyWithProfileSchema`), and the engine already owns the
`deny` in exactly that block, so this is an addition to a block that exists.

```json5
{
  tools: {
    byProvider: {
      "anthropic/claude-opus-5-5": {
        // keep the ten connector patterns the engine writes here
        alsoAllow: [
          "exec", "process", "read", "write", "edit", "apply_patch",
          "web_search", "web_fetch",
          "sessions_list", "sessions_history", "sessions_send", "sessions_yield", "session_status",
          "memory_search", "memory_get", "ask_user", "message",
          "nexgen_sup_start", "nexgen_sup_unit", "nexgen_sup_run",
          "nexgen_sup_collect", "nexgen_sup_review", "nexgen_sup_publish",
          "lazy-mcp__lazy_list", "lazy-mcp__lazy_load", "lazy-mcp__lazy_call", "lazy-mcp__lazy_search"
        ],
        profile: "minimal"
      }
    }
  }
}
```

`profile: "minimal"` is the base allowlist (`session_status`, `gateway` maintenance), and `alsoAllow`
adds the rest, which is the documented way to combine them. Everything Daru does through a connector
(Vault, GitHub, Vercel, Supabase, Playwright) stays reachable through `lazy-mcp`, which is by design the
one road the deny leaves open.

| | Before | After (estimate) |
|---|---|---|
| eager tool schemas | 51 tools, 112,239 chars, ~33,000 tokens | ~28 tools, ~60,000 chars, ~18,000 tokens |
| seed per call | 78,000 | **~63,000 (-19%)** |
| tokens per turn, 74 calls | 5.77 M | **4.66 M** |

This is smaller than it looks on paper because 28 tools is still a lot of schema, and because the other
seven items in the breakdown do not move. A second pass, once Daru really only decides, can take it to
the supervisor plus read tools and land near 12 tools, about 8,000 tokens of schema and a seed near
53,000.

**Feature impact: none lost.** Every capability in the squad's matrix has a tool in the list.

**Caveat for the engine, not the owner**: `agents.entries.capo.tools.alsoAllow` already carries the six
`nexgen_sup_*` names (`tools_profile.py:112-124`). An `allow` list there would conflict with it, since
OpenClaw refuses `allow` beside `alsoAllow` in one scope. Doing this in `tools.byProvider`, as above,
sidesteps the conflict entirely.

### L2. Cap the context a single call can carry

`models.providers.<provider>.models[].contextTokens` caps the active input for one model
(`docs.openclaw.ai/gateway/config-agents/models`: "To cap active input for one model, set
`models.providers.<provider>.models[].contextTokens`"). On the Claude CLI path that number becomes
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` (`cli-backend-BBPDHJCu.mjs:179`,
`resolveClaudeCliAutoCompactEnv`), which is what makes Claude Code compact instead of growing to 1M.

```json5
{
  models: {
    providers: {
      anthropic: {
        models: [
          { id: "claude-opus-5-5", contextWindow: 1000000, contextTokens: 200000 }
        ]
      }
    }
  }
}
```

| | Before | After |
|---|---|---|
| per-call context ceiling | 1,000,000 | 200,000 |
| worst-case cache read for a 74-call turn | 74 M | 14.8 M |
| re-seed after a session reset | full history, up to 1M | full history, compacted at 200k |

**Feature impact: none lost, but a real behaviour change**: older turns become a summary sooner. That is
the trade the owner has to accept, and it is reversible with one key.

**Risk to check before applying**: the `anthropic` provider block normally comes from the bundled plugin.
Declaring a `models` array for it must not replace the plugin's auth route. Verify with
`openclaw config validate` and then one real turn, and revert the key if the model list changes shape.
This is the only lever here that touches provider configuration. If it misbehaves, the fallback is
`agents.entries.capo.compaction` settings, which act later and less precisely.

### L3. Lower the effort from high to medium

`thinkingDefault: "medium"` on capo maps to `--effort medium` (`applyClaudeCliEffortArgs`). Thinking
tokens are output tokens.

| | Before | After (estimate, not measured) |
|---|---|---|
| thinking allowance per call | `--effort high` | `--effort medium` |
| output per call | unmeasured | 30-50% less thinking, so 2-5k tokens less per call |
| output per 74-call turn | unmeasured | **150-370k less** |

**Feature impact: possible quality loss on judgement calls, none on tool use.** This is the one lever
that trades something the owner values, so it belongs to him, not to a default. If he wants the current
behaviour kept, the opt-in shape is `squad.yaml`: `thinkingDefault: high` stays for Daru, and the
mechanical calls are the ones that should move, which is L6, not this.

### L4. Stop paying the re-seed in the middle of the day

Not a new key: a discipline the engine can enforce. Any tool-surface or prompt change resets the native
session and the next turn re-writes the whole history
(`reseedFromRawTranscriptWhenUncompacted`, `docs.openclaw.ai/gateway/cli-backends`).
`openclaw-vps-ottimizzazione.md:100` already records the rule for latency; it applies to quota for the
same reason.

| | Before | After |
|---|---|---|
| config edits during a working session | each one costs one full-history cache write on the next Daru turn | batched at night, one cache write, zero during the day |

**Feature impact: none.** It is a when, not a what.

### L5. Quota pre-check before a Daru turn, and a landing place when the window is gone

`openclaw status --usage` prints the normalized provider windows as "X% left"
(`docs.openclaw.ai/reference/token-use`), and it is read-only. A fallback chain decides where a turn
lands when the primary is exhausted instead of failing halfway:

```json5
{
  agents: {
    entries: {
      capo: {
        model: {
          primary: "anthropic/claude-opus-5-5",
          fallbacks: ["anthropic/claude-sonnet-5-5", "opencode-go/deepseek-v4.1-pro"]
        }
      }
    }
  }
}
```

| | Before | After |
|---|---|---|
| a turn started with 2% of the window left | runs, and either dies mid-loop or eats the reset | the owner sees "2% left" and postpones or accepts the fallback |
| tokens per turn | unchanged | unchanged, but the damage is bounded by a decision instead of discovered afterwards |

**Feature impact: none lost.** A fallback adds a route; it does not remove one.

For the measurement side, do not build a reader: `ccusage` already does it (section 9).

### L6. A per-turn call ceiling: not available first-class, so not done

The one lever that would actually make a Daru turn cost like a local Claude Code turn is a cap on model
calls per turn, and the pinned release does not expose it:

- `claude-cli`'s argv is plugin code. `docs.openclaw.ai/gateway/cli-backends` says so: "Command, argv,
  environment, parsing, session, image, and watchdog mechanics are plugin code registered with
  `api.registerCliBackend(...)`". There is no config key for `--max-turns` or `--max-budget-usd`.
- `agents.defaults.timeoutSeconds` bounds wall-clock, not calls. A fast loop can make a hundred calls
  inside any timeout.
- The loopback server's `alwaysLoad: true` is hardcoded (`mcp-http.loopback-runtime-DxTYlVrU.mjs:203`),
  so Claude's `ToolSearch` cannot defer OpenClaw's own catalogue either.
- Letting Claude Code read the engine's own rendered MCP file instead of OpenClaw's temp file is not
  config either: `bundleMcp: true` is a property of the bundled backend
  (`cli-backend-BBPDHJCu.mjs:106`) and the `--strict-mcp-config --mcp-config` pair is always added
  (`prompt-context-Nx1uhVGB.mjs:119-125`).

So this stays described, not done, as the owner asked. Three shapes are legitimate and all of them are
a real change with a test:

1. an upstream request: a per-agent or per-model "no alwaysLoad" for CLI agents, which is the parity fix
   and would take the seed to roughly the size of Claude Code's own prompt;
2. a small CLI backend plugin that appends `--max-turns N` to a `claude-stream-json` backend, which is
   the documented extension point and would bound the loop at the cost of ending a long job mid-way;
3. an engine guard that reads the transcript after the turn and refuses the next one when the previous
   exceeded a token budget, which is the honest version of what the current first-prompt ceiling tries
   to do.

### Summary table

| Lever | Calls per turn | Tokens per turn | Seed per call | Capability |
|---|---|---|---|---|
| today | 74 | 5.77 M (estimate) | 78,000 | as is |
| L1 allowlist | 74 | 4.66 M | ~63,000 | none lost |
| L1 + L2 | 74 | 4.66 M, ceiling 14.8 M | ~63,000, cap 200k | older turns summarised sooner |
| L1 + L2 + L3 | 74 | 4.66 M, 0.15-0.37 M less output | ~63,000, cap 200k | judgement effort lowered |
| local Claude Code, same work | 74 | 1.85 M at a 25k seed | ~25,000 | n/a: different harness |

## 7. What I measured, what I estimated, what I could not verify

| Figure | Kind | Where it comes from |
|---|---|---|
| 945,389 tokens, first prompt without deny | measured on the VPS, 2026-11-14 | commit `9fd611ea`, `context_budget.py:20-22` |
| 77,840 tokens, first prompt with deny | measured on the VPS, 2026-11-14 | Vault, `claude-cli-quota-burn-2026-10-27.md:74` |
| 74 calls, 65,981,727 cache read, 942,960 first cacheWrite | measured on the VPS, 2026-10-27 | Vault, `openclaw-vps-ottimizzazione.md:188-200` |
| 78,717 tokens first prompt, 64 tools, 51 eager `mcp__openclaw__*`, 112,239 chars of them | measured on this machine, 2026-10-23 | `~/.claude/projects/C--Users-<user>--openclaw-workspace-capo/<session>jsonl`, `api-request-shape` + `assistant.usage` |
| breakdown of the 78k by component | measured characters, tokens derived at ~3.4 chars/token | same transcript, `attachment` records |
| 5.77 M per turn today | estimate: 74 x 78,000 | the two rows above |
| L1, L2, L3 effects | estimate from the character counts and the documented semantics of each key | section 6 |
| today's real call count and cache figures | **not available** | not in the Vault, not on this machine; command in section 8 |
| the VPS `openclaw.json` and `~/.claude` | **not read** | no access from here; the local Windows file is a different machine |
| whether 2026.11.2 still injects Claude's `skill_listing` on managed turns | **unverified** | the docs claim it does not, the code forces `--setting-sources user`, the 2026-10-23 transcript shows it did |

## 8. Read-only verification on the VPS, as user `openclaw`

None of these write anything. Run them in this order; the first four take seconds.

```bash
# 1. The pin actually running
openclaw --version                      # expect 2026.11.2

# 2. Did the deny land, and is anything still eager?
openclaw config get agents.entries.capo.tools.deny
openclaw config get tools.byProvider
openclaw config get models.providers.anthropic.models

# 3. Tool Search: any legacy `false` here is a separate, older wound
openclaw config get tools.toolSearch

# 4. The quota window, which is the only number that decides "may I run Daru now"
openclaw status --usage                 # "X% left" per provider window

# 5. What is scheduled and could spend quota unattended
openclaw automations list --all --json

# 5b. Session sizes as the gateway itself accounts them
openclaw sessions --agent capo --json

# 6. The gateway's tool names per server (read-only preview; it does connect, briefly)
openclaw mcp probe --json | head -100
```

Then the measurement that the engine does not do: per turn, not first prompt. It counts the same
`assistant` records the engine's `context_budget.read_claude_first_prompt` reads, but sums the whole
session and skips the duplicated streaming lines.

```bash
python3 - <<'PY'
import json, pathlib
root = pathlib.Path.home() / ".claude" / "projects"
for proj in sorted(root.glob("*openclaw*")):
    for path in sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-5:]:
        calls, read, write, out, seen, tools = 0, 0, 0, 0, set(), set()
        for line in path.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "assistant":
                m = e.get("message") or {}
                u = m.get("usage") or {}
                if not u or m.get("id") in seen:
                    continue
                seen.add(m.get("id"))
                calls += 1
                read += int(u.get("cache_read_input_tokens") or 0)
                write += int(u.get("cache_creation_input_tokens") or 0)
                out += int(u.get("output_tokens") or 0)
            elif e.get("type") == "api-request-shape":
                tools = {t.get("name") for t in (e.get("shape") or {}).get("tools") or []}
        loopback = sorted(t for t in tools if t and t.startswith("mcp__openclaw__"))
        other = sorted(t for t in tools if t and not t.startswith("mcp__openclaw__"))
        print(f"{path.name[:-6]}  calls={calls}  cacheRead={read:,}  cacheWrite={write:,}  out={out:,}")
        print(f"   tools={len(tools)}  mcp__openclaw__={len(loopback)}")
        print("   not-loopback:", ", ".join(other))
PY
```

What to expect, and what each answer means:

- `calls` near 70 with `cacheRead` near 5-6 M: the tool loop at today's seed, as section 4 predicts.
- `mcp__openclaw__` near 50 with almost nothing in `not-loopback`: the loopback catalogue is still the
  whole tool surface, so L1 is the lever.
- `mcp__outlook__*` or `mcp__n8n-mcp__*` in `not-loopback`: the deny did not land on that path, and the
  engine's own doctor line for the loopback should be red.
- `cacheWrite` in the millions on a session's first call followed by large `cacheRead`: a re-seed after a
  config change (L4).

Inside Daru's chat, two more read-only answers:

```
/context detail     # per-tool schema sizes, per-file bootstrap sizes, skills and system prompt
/usage tokens       # per-reply tokens and cache for the next turns
```

`/context detail` is the one view that says whether the 78k on the VPS is the same composition as the
78k measured here, and specifically whether the skill catalogue is in it.

## 9. GitHub check: is there a component we should not write?

The measurement and the pre-check do not need new engine code.

**`ccusage` (`ccusage/ccusage` on GitHub, MIT, 19.4k stars, `npx ccusage@latest`)** reads the JSONL of
Claude Code, Codex, OpenCode, Antigravity, OpenClaw and a dozen other harnesses, and has exactly the two
reports this problem needs: a `session` report with cache read and cache write per session, and a
`blocks` report for Claude's 5-hour billing windows.

```bash
npx ccusage@latest session --json                      # per-session tokens, cache read/write included
npx ccusage@latest claude daily --instances --json      # Claude Code, grouped by project
npx ccusage@latest blocks                               # the 5-hour windows, "how much is left"
npx ccusage@latest openclaw daily --json                # the gateway's own accounting
```

It is read-only, it needs no credential, and it is the component the engine's `context_budget` module
partly reimplements (first prompt only). The proposal, if the owner wants it: use `ccusage` for the
per-turn and per-window numbers, and keep the engine's check as the cheap pass/fail it already is.

Alternatives I looked at in a GitHub search for "claude code token usage dashboard" (563 repositories) and did
not prefer: `splunk/token-meter` and `zhangferry/tokendash` are local dashboards over the same JSONL
(and `tokendash` already parses OpenClaw), which answers "where did it go" but not "how many calls did
this turn make", and neither publishes the per-session cache figures as JSON in the form a script or a
doctor check consumes. I did not verify their licences, so they are not proposed. `ccusage` is MIT and
has the two reports this problem needs.

Nothing else is proposed as new code. L1 to L5 are configuration, and L6 is deliberately not built.

## 10. What only the owner can decide

1. **L3, effort high to medium.** It is the one lever that can cost judgement quality. Keep high for
   Daru and take the other levers, or lower it and keep the window.
2. **The allowlist content in L1.** The list above keeps every capability in the squad matrix. If Daru
   no longer needs to execute, the second pass is much cheaper, and that is a decision about the role,
   not about tokens.
3. **L2's 200k cap.** It changes when a conversation becomes a summary. If 200k is too aggressive,
   300k still removes the 1M worst case.
4. **Whether to ask upstream for the parity fix** (no `alwaysLoad` for a CLI agent's own tool
   catalogue), which is the only change that makes a Daru turn cost what the same turn costs in local
   Claude Code.
