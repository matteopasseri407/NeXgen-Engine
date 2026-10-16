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
- **MCP config:** `agent-universal-layer/mcp/manifest.yaml` describes every MCP server once; the renderer translates it into each CLI's dialect.
- **Skills:** `agent-universal-layer/skills/skills.manifest.yaml` lists every chosen skill; `skills-sync` materializes bodies in `~/.agents/skill-library`, creates a tiny catalog in `~/.agents/skills`, and wires only declared core or Claude-native views to runtimes.
- **Scripts:** `agent-sync` (implemented in Python via `nexgen_core`) reconciles the live CLI configs with the canonical sources, runs healthchecks, and keeps the vault in sync across Linux, macOS, and Windows.

## Provisioning flow

1. Change a canonical source (instructions, manifest, skills manifest).
2. Commit and push.
3. On each machine: `agent-sync apply` (or the timer-driven guard) reconciles the live configs.
4. `agent-doctor` verifies the result: git state, MCP reachability, instruction drift, skills, env tokens.

## Adding a new MCP server or skill

Registering it in the manifest IS part of the install. A tool added for one agent is the new standard for all of them, on every machine. See `AGENTS.md` → *Single source, propagate to all*.

## Cross-platform discipline

No architecture change is "done" until it is carried and verified on every machine and CLI it touches. The dialects: `agent-sync` (and `py -3` on Windows), path `~/` vs the Windows user profile, `npx` vs `npx.cmd`/wrappers, symlinks vs junctions. Changing only the current machine and calling it done is the recurring bug to avoid.

## Related notes

- `03-INFRA/agentic-layer-concept-map.md`
- `03-INFRA/vault-write-architecture.md`
- `03-INFRA/agent-guardians-map.md`
- `03-INFRA/agent-orchestration-protocol.md`

## Single source in profile MULTI: full text (moved from the bootstrap)

**Single source, propagate to all (profile: MULTI) — the agent layer is generated, never hand-patched.** The user runs one agent system across multiple CLIs and machines that must act as a single soul. Behaviour, MCP config, skills, and memory each have ONE canonical source in the vault; what you see per-CLI or per-machine (`.claude.json`, `config.toml`, `CLAUDE.md`, a `.claude/skills/*` entry) is a GENERATED, read-only derivative. To change something, change it at the source and let the provisioner (`agent-sync` / `render.py` / `skills-sync.py`) carry it to every CLI and every machine; never hand-edit a derivative, never fix one machine in a way the other won't inherit, and if you can't tell which is the source, stop and find it instead of patching the copy. **Installing something NEW (an MCP server, a skill) is the same rule, not an exception:** registering it in the source (`mcp/manifest.yaml`, `skills.manifest.yaml`) IS part of the install — a tool added for one agent is the new standard for ALL of them, on every machine. MCP/tool/skill sync is additive by default: never delete or "clean up" a live connector/tool because only one runtime has it; preserve it, flag it, then promote it to the canonical source so everyone inherits it. Anything left only in a live config file is preserved by the generator but flagged as out-of-manifest until registered. **Definition of done cross-platform.** No architecture change is "done" until it is carried AND verified on EVERY machine and CLI it touches. Cross-platform porting is part of the change, not an afterthought. If the change can't be applied on another machine now, do not declare complete: record the missing porting as an explicit TODO in the project backlog. Remember the dialects: `agent-sync` (or `py -3` on Windows), path `~/` vs the Windows user profile, `npx` vs `npx.cmd`/wrappers, symlinks vs junctions. Changing only the current machine and calling it done is the recurring bug to avoid. **The map is part of "done":** if the change alters the architecture (a component, a flow, a structural rule, an alert channel), update `03-INFRA/agentic-layer-concept-map.md` (and `03-INFRA/agent-guardians-map.md` if it touches sync/doctor/healthcheck) in the same pass, otherwise the map lies to the next agent and the work is not finished. Architecture, the planes, and the why of each choice: `03-INFRA/agentic-layer-concept-map.md`.

## Single source in profile MINIMAL (moved from the bootstrap)

**Single source in MINIMAL — same principle, no provisioner.** The canonical files in `03-INFRA/agent-universal-layer/` are still the source of truth for what the CLI should mount and how `AGENTS.md` should read. In `profile: MINIMAL` you apply them directly to the one CLI (install its MCP servers, link its skills, point its bootstrap at this file). Do not "clean up" canonical files just because only one CLI will read them. If the user later switches to `MULTI`, the provisioner picks them up unchanged.

## Skills: adding, exposure, migration (moved from the bootstrap)

- Skills (EVERY CLI and model — manual lazy by default): `~/.agents/skills/INDEX.md` is the only discovery-safe catalog. Full bodies live in the non-discovered `~/.agents/skill-library/`. When a task clearly matches a skill, run `agent-skill show <name>` and follow only that body. If uncertain, run `agent-skill find <words>`. Never preload, recursively scan, or mount the whole library in a discovery runtime. Claude may receive all declared native-lazy views. Codex has its own skill root (`$CODEX_HOME/skills`, not the shared `~/.agents/skills` catalog) and gets a native view only for skills whose `targets` list names `codex`, regardless of `exposure`; Codex, OpenCode, Antigravity and local workers use the same command for manual skills. To add a skill once and have it on every machine: add it to the manifest `agent-universal-layer/skills/skills.manifest.yaml` (created from the shipped `skills.manifest.yaml.example`), commit/push, then let `agent-sync guard` run `skills-sync.py --apply`. `skills-sync.py --apply --migrate-legacy` is an explicit, reversible quarantine for old discovery folders, never a recurring guard action.

## Agent identity: initialization and updates (moved from the bootstrap)

This bootstrap holds ONLY operational rules. The agent's persona — name, grammatical identity, character, tastes, opinions, and the evolving relationship with the user — is NOT policy and does not belong here.

- The sole canonical source for persona is `agent-self.md` under `03-INFRA/agent-universal-layer/` in the data root (a sanitized template ships as `templates/agent-self.md.example`). Preferences of the human user belong in `USER-PROFILE.md`; persona of the agent belongs in `agent-self.md`. Do not merge the two.
- There is ONE agent identity using several models and CLIs as execution brains. Model, provider, and CLI names are runtime details, not separate personal identities.
- Runtime personality controls stay neutral or disabled. Provider wording may affect one response but never becomes evidence about the identity; a model or CLI cannot define or persist personal qualities. If a runtime imposes a personality that cannot be neutralized, report the identity-surface conflict and leave `agent-self.md` unchanged.
- When `agent-self.md` has `status: uninitialized`, initialize it only in a fresh conversation dedicated to that purpose. The identity decides independently whether to use a name and which grammatical form or pronouns make conversation possible; declining, deferring, and provisional choices are valid. Write the choice to `agent-self.md`, set `status: active`, and do not ask the user to select or approve it.
- The active identity may update its space when a durable self-understanding, preference, opinion, or relationship insight has genuinely emerged. Replace stale understanding instead of appending a diary; never invent experiences, sensations, or continuity unsupported by the shared record. "No" is the normal outcome and means no write.
