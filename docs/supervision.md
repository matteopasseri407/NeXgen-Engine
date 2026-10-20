# Supervised tasks (phase 1)

A supervised job is one objective carried out by harness agents that the owner
can watch: a frontier model writes the brief, an executor works in a window of
its own harness, and the engine keeps the files and the money. This document
covers **phase 1 only**: the task folder, the visible windows and the cost
register. Nothing here runs an executor, executes gates or publishes.

## Where a task lives

`nexgen-local sup start "<objective>"` creates a folder under the lane's state
(`~/.local/state/nexgen/local-lane/supervision/<id>/`, id = timestamp + a slug
of the objective) with four files:

| file | owner | what it holds |
|---|---|---|
| `00-obiettivo.md` | the engine | the objective, the id and when it started |
| `01-diario.jsonl` | the engine | one JSON line per event, append-only |
| `20-brief-MODELLO.md` | the supervisor | the brief fields, empty, one per unit |
| `30-resoconto-MODELLO.md` | the executor | the report fields, empty |

The journal is the machine truth: `started`, then one `attached` row per
harness session bound to a unit. The Markdown files are what a person reads
and writes; the engine never rewrites them, so an edit is not lost.

## The verbs

```bash
nexgen-local sup start "porta la fase 1 dentro il motore"   # id and folder
nexgen-local sup status                                     # every task, one line each
nexgen-local sup status <id>                                # objective, units, sessions, last event, cost
nexgen-local sup attach <id> <unit> --role esecutore --opencode-session <ses_...>
nexgen-local sup costi <id>                                 # total, then by role and by unit
nexgen-local sup window-check                               # opens a visible window and leaves it open
```

`sup attach` writes the journal event **and** one row in `05-costi.jsonl`.
`sup status` and `sup costi` never compute a price of their own: they read what
the harness reported.

## Visible windows

`supervision_windows` opens a terminal that runs a command and stays open; the
engine never reads its output, and a window that cannot be opened raises
instead of falling back to a hidden process.

- Windows: `wt.exe` (new window, title, working directory, then `pwsh`
  with `-NoExit`, or `powershell` when 7 is missing), otherwise a dedicated
  console (`CREATE_NEW_CONSOLE`).
- Linux: the first of `x-terminal-emulator`, `gnome-terminal`, `konsole`,
  `xterm`, each with its own flags, the shell kept alive with `; exec bash`.
  Without `DISPLAY` and without `WAYLAND_DISPLAY` it refuses: a window nobody
  can see is a hidden process with extra steps.
- The command is passed as argv, so the quoting rule is the engine's own
  (`nexgen_core.processes`), not a hand-built string.

`sup window-check` is the manual proof on each machine: one window opens,
prints `finestra del supervisore OK` and stays open. It is never part of a
test: the tests substitute `Popen` and `which`, so no suite opens a window.

## The cost register

One row per attach in `05-costi.jsonl`: instant, task, unit, role, harness,
model, session, window, duration, tokens in and out (with reasoning and cache
read/write), cost in dollars, and the source of the measurement. The source of
this phase is `opencode session export <id>`, resolved through PATH and through
the engine's Windows shim resolver.

A source that does not answer still leaves a row, with `cost_usd: null` and a
`note` naming the reason. A missing measurement is never written as zero, and
`sup costi` says how many rows had no source.

## What phase 1 does not do

- No worktree, no local branch per unit, no executor profile: `sup run` and the
  permission posture belong to phase 2.
- No gates on a commit interval, no review package, no approval loop: phase 3.
- No LangGraph, no resume: the state is files, so a restart reads them.
- No `sup publish`: the supervisor publishes by hand until phase 3.
- Only the OpenCode harness is measured. The scout (`agy`) and the supervisor
  CLI stay unmeasured and would be recorded as unavailable.
- No cleanup of old task folders: the retention decision (30 days) belongs to
  the same rule as research sessions and is not implemented here.
