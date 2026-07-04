---
tags:
  - infra
  - agents
  - routing
  - cost
status: active
type: protocol
---

# Model Routing & Cost Strategy

Evidence and rationale for the routing discipline in `03-INFRA/agent-orchestration-protocol.md`. The user fills in their available models in `99-INDEX/USER-PROFILE.md`; this note records the reasoning framework, not specific model names.

## Objective

Maximum verified high-quality output per scarce frontier token. Do not ship mediocre work to save tokens; do not burn frontier quota on work a cheaper tier or a deterministic command can do safely.

## Cost architecture

- **Frontier CLIs** (pay-per-token or subscription): the scarce resource. Token discipline matters.
- **Cheap CLI plans** (flat fee, windowed cap): the scarce resource is the cap, not dollars-per-token. Inside the cap, tokens do not cost out of pocket. Do not move mechanical micro-tasks to a metered API on top of the flat fee.
- **Direct API** (metered): use for big batches that would devour the cheap CLI cap, or for automation pipelines that cannot use a CLI plan.
- **Local worker** (free, offline): fallback only.

## Routing rules

1. Task-based, not vertical escalation. Match the task to the tier immediately.
2. Micro-task → cheap CLI. Big batch → direct API. Hard work → frontier.
3. Keep cheap CLI sessions open and reuse them when caching makes long sessions cheap. Do not start a fresh session for every small question (each pays a bootstrap cost).
4. Retired tiers: if a model burns the cap without delivering frontier quality, do not route hard work to it. Escalate directly to frontier.
5. Automations and pipelines outside a CLI plan must use a direct API with capped spending and secrets managed outside notes.

## Secrets

API keys live in env vars or the encrypted archive, never in notes or configs. Reference by env var name only. See `AGENTS.md` → `# Secrets`.

## Notes correlated

- `03-INFRA/agent-orchestration-protocol.md`
- `03-INFRA/batch-vs-go-routing.md`
