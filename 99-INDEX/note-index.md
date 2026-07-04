---
tags:
  - index
  - map
status: active
type: index
---

# Note index — the vault map

One line per canonical note, grouped by area. The gardener
(`03-INFRA/vault-grooming-playbook.md`) keeps this in sync: when a note is
added, merged, archived, or renamed, update the matching line here. This is the
cheap "whole-vault view" every grooming run reads first.

Format: `path — one-line purpose`. Keep it to one line per note; do not let it
grow into prose.

## 03-INFRA — the engine
- `03-INFRA/agent-universal-layer/instructions/AGENTS.md` — canonical agent bootstrap.
- `03-INFRA/agentic-layer-concept-map.md` — architecture map (the three planes, the why).
- `03-INFRA/agent-guardians-map.md` — what watches sync/doctor/healthcheck.
- `03-INFRA/vault-write-architecture.md` — how writes reach the vault (one door per type).
- `03-INFRA/vault-grooming-playbook.md` — the gardener's run.
- `03-INFRA/deploy/README.md` — self-hosted stack (Cloud-Server mode).

## 99-INDEX — the map
- `99-INDEX/USER-PROFILE.md` — identity, hardware, paths, preferences.
- `99-INDEX/agent-retrieval-protocol.md` — lexical vs semantic retrieval.
- `99-INDEX/vault-cleanup-backlog.md` — grooming decisions already made.

## 01-NOTES / 02-PROJECTS / 04-NOW
- Add your notes here as you create them, one line each.
