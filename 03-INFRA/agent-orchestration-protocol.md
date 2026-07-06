---
tags:
  - infra
  - agents
  - routing
status: active
type: protocol
---

# Agent Orchestration Protocol

How to route work across the model team. The user configures their available models in `99-INDEX/USER-PROFILE.md`; this note defines the discipline, not the specific model names.

## Principle: task-based (horizontal) routing

Match the task to the right model immediately, rather than "start cheap and fail up". Routing is task-based, not vertical escalation.

## Tiers

- **Frontier (reasoning/architecture):** blank-page design, deep reasoning, logic trees, massive doc ingestion, strategy, security/auth/secrets, irreversible actions, final judgment. Start here for hard design.
- **Frontier (orchestration/build):** complex tool chaining, multi-file refactoring, API error recovery, UI/design judgment. Start here for complex orchestration.
- **Mid-tier (component execution):** writing single components, specific algorithms, daily reversible coding.
- **Frontier (terminal/sysadmin):** autonomous terminal operations, Docker debugging, remote backend maintenance.
- **Bulk (mechanical data):** repetitive extraction, log sweeping, classification, scraping loops. Strictly volume work.
- **Deterministic (L0):** shell, `rg`, `jq`, tests, scripts. No model.

## Rules

1. Design goes to frontier reasoning. Orchestration goes to frontier build. Component writing goes to mid-tier. Terminal goes to frontier terminal. Bulk data goes to bulk.
2. Do not burn frontier quota on work a cheaper tier or a deterministic command can do safely.
3. Do not ship mediocre work to save tokens. If the cheap tier cannot do it well, escalate.
4. If a cheap CLI session hits quota or quality limits, spill over to a frontier CLI or a direct API, do not retry blindly inside the capped session.
5. Two failed attempts on a cheap tier → stop and return evidence for frontier escalation.

## Handoff to a cheaper model

When the frontier agent decides a cheaper model should do the heavy work, use the handoff template: `03-INFRA/agent-universal-layer/templates/cheap-model-handoff.md`. Keep the handoff narrow: objective, scope (read/edit/do-not-touch), task, verification commands, output format, budget rules.

## Return contract

When a cheaper agent or subagent returns work, it brings back: result, files touched, commands run, verification result, risks/uncertainty, recommended frontier review points. Conclusions, never raw dumps — this protects the coordinator's context and the user's review bandwidth.

## Batch offload

Before launching a repetitive, high-volume, tool-less batch (multi-item translation/extraction/classification, audits across many files, large log sweeps), offload it to a cheap direct API instead of burning the interactive session's quota. Micro-task → interactive; big batch → direct API. Full rule in `03-INFRA/batch-vs-go-routing.md`.

## Related notes

- `03-INFRA/model-routing-cost-strategy.md`
- `03-INFRA/agent-universal-layer/templates/cheap-model-handoff.md`
- `03-INFRA/batch-vs-go-routing.md`
