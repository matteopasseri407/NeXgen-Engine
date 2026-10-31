# Lazy skills

🇮🇹 [Italiano](it/lazy-skills.md)

Skills are optional task playbooks. NeXgen keeps the always-on layer small: `AGENTS.md` carries policy and tool
awareness, the MCP manifest carries tools, and a skill body is opened only when the task matches it.

## Layout

| Location | Purpose |
|---|---|
| `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` | Canonical, cross-machine choice of skills. |
| `~/.agents/skill-library/` | Complete managed bodies. Never a discovery root. |
| `~/.agents/skills/INDEX.md` | Small generated catalog. No optional `SKILL.md` bodies live beside it. |
| `~/.claude/skills/` | Native Claude views, when Claude is a target. |
| `~/.codex/skills/` (or `$CODEX_HOME/skills`) | Native per-skill views, when Codex is a target. This is Codex's only skill root: it does not read `~/.agents/skills`. |
| `~/.gemini/antigravity-cli/skills/` | Native Antigravity views, when `antigravity` is a target. They surface as `/name` commands in the agy TUI. |

Every manifest entry has an `origin`, optional `targets` and an `exposure`.

`targets` decides where a view is written, and it is the only thing that does. A declared skill gets a view in every
runtime it names. Claude, Codex and Antigravity each read a folder of their own, so a skill that does not name them is
not discoverable there. OpenCode and OpenClaw read the shared `~/.agents/skills` catalog as well as their own folders.

`exposure` is recorded in the manifest and shown in `INDEX.md`. It does not gate the views: a declared skill gets
them whatever its exposure, because pruning declared skills made them silently unreachable after every sync. Loading
is routed by `INDEX.md` plus on-demand reads.

- `lazy` is the default.
- `eager` and `core` mark the starter commands the engine showcases in `~/.agents/skills`.
- `manual` marks entries kept out of that display, such as the deprecated aliases.

Two more optional fields exist for a small team sharing this framework:

- `scope: personal | team`, default `team`. Any value other than `personal` propagates to every machine.
- `owner: <member-id>`, the identifier of whoever a skill is personal to.

`scope: personal` filters only when `AGENT_TEAM_MEMBER` is set. A personal skill is materialized on the machine whose
`AGENT_TEAM_MEMBER` matches its `owner`, skipped everywhere else with a line saying so, and materialized everywhere
when the variable is unset.

Any managed skill can be found and read on demand:

```bash
nexgen skill list                    # elenca tutte le skill gestite
nexgen skill find debugging          # richiede almeno un termine
nexgen skill show systematic-debugging
```

(Historical names `agent-skill list|find|show` still work as aliases.)

## Cross-CLI command skills

Every runtime NeXgen renders for consumes the same agentskills.io shape (`<name>/SKILL.md`, frontmatter `name` and
`description`) and lets the user invoke a discovered skill explicitly. Declare a skill once and the same procedure is
invocable everywhere:

```yaml
my-command:
  origin: vault
  targets: [claude, codex, antigravity, opencode, openclaw]
  exposure: core
```

- `claude` links the view for Claude Code (`/my-command`).
- `codex` links it into `$CODEX_HOME/skills` (default `~/.codex/skills`), Codex's only skill root, where it surfaces
  as a `$my-command` mention.
- `antigravity` links it into `~/.gemini/antigravity-cli/skills/` (`/my-command` in the agy TUI).
- `opencode` links it into `~/.config/opencode/skills/` and the shared `~/.agents/skills/` catalog, and the sync
  verifies the skill is discoverable there.
- `openclaw` is that same shared catalog: OpenClaw discovers `~/.agents/skills` on its own, so naming it lands the
  skill there even when no other runtime is named.

Keep a command portable: lowercase-hyphen names matching the folder name, never a CLI built-in name (Claude Code
ships a bundled `/doctor` that a same-named skill would override), and a body written without arguments, "the text
after the command is the request", because placeholder syntaxes like `$ARGUMENTS` differ per CLI while plain
instructions behave the same everywhere.

The engine ships starter commands, registered in `skills.manifest.yaml.example`. Copy that file to
`skills.manifest.yaml` in the same folder to get them on a fresh install. The engine never rewrites an existing
manifest, so emptying it to `skills: {}` is a permanent opt-out. The starters are `nexgen-doctor`, `vault-close`,
`vault-save`, `nexgen-council`, `nexgen-discipline`, `vault-groom`, `nexgen-update` and `vault-map`. Each body encodes
the documented runbook of the tool it wraps, so the guarded flows stay guarded. The earlier names `vault-doctor`,
`vault-council` and `vault-update` still resolve as deprecated aliases.

`/nexgen-update` is the chat-facing skill. Its guarded work runs through the cross-platform `nexgen update` command
(historical name `nexgen-update`, still installed), so both entry points share one updater.

## Synchronization and migration

Change the manifest and the source body in the Vault, then commit and push that canonical change. On each machine,
`nexgen guard` (historical name `agent-sync guard`) reconciles the managed entries only.

An existing installation may still have old folders in discovery roots. Run the normal provisioner first, so it
repairs any old whole-root link, then move the old folders out with the one-time migration:

```bash
nexgen guard
nexgen skill sync --migrate-legacy
```

That preserves unknown folders under `~/.agents/skill-library/legacy/`, outside the catalog and the runtime roots.
Review them deliberately: promote a worthwhile one into the pinned manifest, or delete it later with explicit user
approval. The recurring guard never runs that migration itself.

Run `nexgen doctor --strict --summary` (historical name `agent-doctor --strict --summary`) after the rollout. A
cross-machine change is complete only when you have verified the same result on every declared machine and CLI.
