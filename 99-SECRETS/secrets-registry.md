---
tags:
  - index
  - secrets
status: active
type: registry
---

# Secrets registry (non-sensitive)

Index of the secrets this vault points at. **Names and env vars only — never
values** (policy: `AGENTS.md` → Secrets). This file is git-tracked; values
never are (they stay in per-machine keyrings/env stores — see `README.md`
in this folder).

| Name | Provider | Env var | Scope | Last rotated | Notes |
|---|---|---|---|---|---|
| _(example — delete this row)_ | OpenAI | `OPENAI_API_KEY` | all machines | 2026-01-01 | LLM extract endpoints |
