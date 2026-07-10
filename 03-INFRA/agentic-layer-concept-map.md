---
tags:
  - infra
  - architecture
  - map
status: active
type: map
---

# Agentic Layer Concept Map

Logical map + technical choices and their *why*. For the write flow in detail: `03-INFRA/vault-write-architecture.md`. For the project, register and backlog: `02-PROJECTS/`.

## Principle: one soul, many machines

The user runs one agent system across multiple CLIs and machines that must act as a single soul. Behaviour, MCP config, skills, and memory each have ONE canonical source in the vault; what each CLI or machine sees is a GENERATED, read-only derivative.

## Topology

```
                          ┌─────────────────────────────────────┐
                          │   THE USER (human-in-the-loop)      │
                          └───────────────────┬─────────────────┘
                                              │
        ┌─────────────────────────────────────┴─────────────────────────────┐
        │  MACHINE A (e.g. laptop)              MACHINE B (e.g. desktop)     │
        │  mobile / fallback                    workstation                  │
        │  local worker: on-demand only         local worker: on-demand only │
        └──────────────┬──────────────────────────────────────┬─────────────┘
                       │      (same layer on both)            │
        ┌──────────────┴───────────────┐              ┌───────┴────────────────┐
        ▼              ▼               ▼              ▼        ▼              ▼
   ┌─────────┐  ┌──────────┐  ┌────────────┐  ┌──────────┐  (idem on the other machine)
   │ CLI 1   │  │  CLI 2   │  │   CLI 3    │  │  CLI 4   │
   │frontier │  │frontier  │  │ reasoning  │  │  cheap   │
   └────┬────┘  └────┬─────┘  └─────┬──────┘  └────┬─────┘
        └────────────┴───────┬──────┴──────────────┘
                             │  UNIVERSAL LAYER (source in vault, derivatives read-only)
        ┌────────────────────┼─────────────────────────────┬──────────────────┐
        ▼                    ▼                             ▼                  ▼
   BEHAVIOUR            CONFIG (MCP)                  MEMORY              HOOKS
   AGENTS.md        manifest.yaml +               KnowledgeVault      checkpoint hook
   (1 file,         render.py →                   (markdown notes)    (per-CLI, optional)
   every CLI)       per-CLI dialect
```

## The three planes

1. **Behaviour** — `AGENTS.md` is the single bootstrap. Every CLI's pointer file references it. One file, every agent, drift impossible.
2. **Config** — `mcp/manifest.yaml` describes every MCP server once; `render.py` translates it into each CLI's dialect. Every local MCP package launched through `npx` has an exact version pin, so an upgrade is a tested engine change rather than an implicit upstream update. `skills/skills.manifest.yaml` does the same for skills. GitHub skills declare a full commit SHA, and `skills-sync.py` fetches and checks that exact object before materializing it.
3. **Memory** — the KnowledgeVault (markdown notes, Git-backed). Written through one door per type: notes via the `vault-library` MCP, infra files via `vault-push`.

## Why one source

Hand-patching per-CLI configs creates drift: one CLI behaves differently from another, one machine falls behind, a fix on one side does not propagate. The single-source + provisioner model means a change is made once and carries everywhere. The cost is the provisioner machinery; the benefit is a system that stays coherent as it grows.

## Council prompt transport

Council keeps the full user brief out of the operating system command line. Codex and Antigravity receive it through stdin. OpenCode receives a protected temporary attachment, because that is its documented non-argv interface. The attachment is created inside the private session tree and removed after the seat exits, including on failure. This prevents Windows and POSIX command-line limits from turning a valid large review into an opaque invocation error, while keeping the existing ephemeral-session policy intact.

## Guardians

- **`agent-sync`** — reconciles live configs with the canonical sources on each machine.
- **`agent-doctor`** — the single diagnostic: git state, MCP reachability, instruction drift, env tokens, skills, local worker. The only command to run by hand when something seems off.
- **healthcheck step (inside `agent-sync`)** — grouped health summary; sends an alert only on FAIL. Was a standalone `agent-healthcheck.sh`, folded into `agent_sync.py`.
- **`vault-lifecycle-audit.py`** — read-only heat-map for vault grooming candidates.

Full guardian map: `03-INFRA/agent-guardians-map.md`.

## Cross-platform definition of done

No architecture change is "done" until it is carried and verified on every machine and CLI it touches. The map is part of "done": if a change alters the architecture, update this map in the same pass.

## Related notes

- `03-INFRA/vault-write-architecture.md`
- `03-INFRA/agent-guardians-map.md`
- `03-INFRA/agent-universal-layer.md`
- `03-INFRA/agent-orchestration-protocol.md`
