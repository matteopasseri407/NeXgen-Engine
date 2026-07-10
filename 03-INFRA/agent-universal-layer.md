---
tags:
  - infra
  - agents
  - state
status: active
type: state
---

# Agent Universal Layer — state

This note records the state and evolution of the agent universal layer: the single-source provisioning system that makes every CLI and every machine act as one soul.

For the architecture and the why of each choice, see `03-INFRA/agentic-layer-concept-map.md`. For the write flow, see `03-INFRA/vault-write-architecture.md`.

## What it is

The universal layer is a set of canonical sources in the vault plus provisioners that generate per-CLI, per-machine derivatives:

- **Instructions:** `agent-universal-layer/instructions/AGENTS.md` is the single bootstrap. Each CLI's pointer file (`~/CLAUDE.md`, `~/.codex/AGENTS.md`, etc.) references it. One file, every agent, drift impossible.
- **MCP config:** `agent-universal-layer/mcp/manifest.yaml` describes every MCP server once; `render.py` translates it into each CLI's dialect.
- **Skills:** `agent-universal-layer/skills/skills.manifest.yaml` lists every chosen skill; `scripts/skills-sync.py` materializes bodies in `~/.agents/skill-library`, creates a tiny catalog in `~/.agents/skills`, and wires only declared core or Claude-native views to runtimes.
- **Scripts:** `scripts/agent-sync.sh` (Linux/Mac) and `scripts/agent-sync.ps1` (Windows) reconcile the live CLI configs with the canonical sources, run healthchecks, and keep the vault in sync.

## Provisioning flow

1. Change a canonical source (instructions, manifest, skills manifest).
2. Commit and push.
3. On each machine: `agent-sync apply` (or the timer-driven guard) reconciles the live configs.
4. `agent-doctor` verifies the result: git state, MCP reachability, instruction drift, skills, env tokens.

## Adding a new MCP server or skill

Registering it in the manifest IS part of the install. A tool added for one agent is the new standard for all of them, on every machine. See `AGENTS.md` → *Single source, propagate to all*.

## Cross-platform discipline

No architecture change is "done" until it is carried and verified on every machine and CLI it touches. The dialects: `agent-sync.sh`/`.ps1`, path `~/` vs the Windows user profile, `npx` vs `npx.cmd`/wrappers, symlinks vs junctions. Changing only the current machine and calling it done is the recurring bug to avoid.

## Related notes

- `03-INFRA/agentic-layer-concept-map.md`
- `03-INFRA/vault-write-architecture.md`
- `03-INFRA/agent-guardians-map.md`
- `03-INFRA/agent-orchestration-protocol.md`
