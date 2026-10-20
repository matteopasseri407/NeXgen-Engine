# Supervised tasks (phases 1 and 2)

A supervised job is one objective carried out by harness agents that the owner
can watch: a frontier model writes the brief, an executor works in a window of
its own harness, and the engine keeps the files, the money and the verdict.
This document covers **phases 1 and 2**: the task folder, the visible windows,
the cost register, the unit's working copy, and the gates the engine runs before
anybody reviews a diff. Nothing here publishes or approves anything.

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

A unit adds `20-brief-<unit>.md` (the brief as written), `22-ambiente-<unit>.md`
(where the copy is, on which branch, inside which boundary), `40-pacchetto-<unit>.md`
and `50-gate-<unit>.txt` (the review package and the raw gate output), plus its
own working copy in `23-unita-<unit>/`.

The journal is the machine truth: `started`, `unit_created`, `unit_started`,
`attached`, `unit_collected`. The Markdown files are what a person reads and
writes; the engine never rewrites them, so an edit is not lost.

## The verbs

```bash
nexgen-local sup start "porta la fase 2 dentro il motore"   # id and folder
nexgen-local sup status                                     # every task, one line each
nexgen-local sup status <id>                                # units, states, verdicts, costs
nexgen-local sup unit <id> <unit> --repo <checkout> --base <ref> --brief <file> \
    --allow 'docs/*.md' [--allow ...] [--verify "<command>"]
nexgen-local sup run <id> <unit>                            # the executor's window
nexgen-local sup collect <id> <unit>                        # the engine's gates, exit 1 on FAIL
nexgen-local sup attach <id> <unit> --role esecutore --opencode-session <ses_...>
nexgen-local sup costi <id>                                 # total, then by role and by unit
nexgen-local sup window-check                               # opens a visible window and leaves it open
```

`sup attach` writes the journal event **and** one row in `05-costi.jsonl`.
`sup status` and `sup costi` never compute a price of their own: they read what
the harness reported.

## The unit's working copy

`sup unit` creates a git worktree from the resolved `--base` commit, on a local
branch `sup/<task>/<unit>`, inside the task folder (`23-unita-<unit>/`). One
folder per task means one cleanup, and nothing appears next to the owner's
checkouts. The branch is created from a commit, not from a branch name, so a
branch that moves later cannot change what was reviewed.

The push is blocked inside that copy alone, with the one mechanism git offers
per worktree: `extensions.worktreeConfig` (a repository-wide switch that
changes nothing until a worktree config exists) plus
`remote.<name>.pushurl = nexgen-sup-disabled://push-blocked` in the worktree's
own `config.worktree`. A push from the copy fails with
`remote helper 'nexgen-sup-disabled' aborted session`; the owner's checkout
keeps its real remote. Shared git hooks are never touched: they live in the
common directory and stay exactly as the owner installed them.

The refs of every remote are read with `git ls-remote` when the unit is created
and written into the journal. `sup collect` reads them again and compares: a
remote that moved, or one that cannot be read, is a FAIL, never an assumption.

Cleanup stays manual and explicit: `git worktree remove <path>` and
`git branch -D sup/<task>/<unit>` from the repository.

## The executor's window

One harness means one session per task, and the engine keeps it that way.

`sup run` looks for the session the task already owns. If there is one and it
is idle (`time.idle` at or past `time.updated` in the harness's own export),
the unit's work goes into it with `opencode run -s <session> "<pointer>"` and
no new tab is opened. If it is busy, the unit is **queued**: the journal records
`unit_queued` with the reason, `sup status` shows `in coda`, and the engine says
so instead of opening another window. A tab is opened only when the task has no
session for that harness yet:

```
opencode <worktree> --prompt "Sei l'esecutore dell'unità ... leggi il brief in ..."
```

The prompt is a pointer to `20-brief-<unit>.md`, never the brief itself, and no
`--auto` or `--dangerously-*` flag is ever passed: the model and the permissions
stay what the owner configured.

The session that a new tab opens is found in `opencode session list --format
json`, which reports `id`, `created` and `directory`: the newest session whose
`directory` is the unit's copy and whose `created` is at or after the moment the
tab was opened. When no session matches within the wait (30 seconds), `sup run`
says so and the attach stays a manual `sup attach`: the engine never guesses
which session belongs to which unit. The cost of the attached session lands in
`05-costi.jsonl` like any other attach.

## The engine's gates

`sup collect` judges the unit's `base..head` itself. The executor's report is
not evidence; every line below is read from git or run by the engine.

| gate | fails when |
|---|---|
| `confine` | a touched file (both names of a rename) is outside every `--allow` glob |
| `file vietati` | `VERSION`, `CHANGELOG.md` or `.github/**` changed and the boundary does not name it |
| `integrità dei test` | an existing test was deleted, shrunk or skipped (`nexgen_core/discipline/integrity.py`, reused) |
| `prova per commit` | a `fix:` commit has no test that fails on its parent (`nexgen_core/discipline/regress.py`, reused) |
| `verifica indipendente` | the declared command is missing, or exits non-zero, run by the engine with its own launch rules |
| `remoto intatto` | a remote's refs differ from the creation snapshot, or cannot be read |
| `albero pulito` | the unit's copy has uncommitted work |

The verdict is PASS only when every line is. The package
(`40-pacchetto-<unit>.md`) carries the verdict, the gate lines, the diffstat,
the diff with a declared cap (400 lines) and the cost of the attached session;
`50-gate-<unit>.txt` carries the raw output, including what the verification
printed. `sup collect` exits 1 on FAIL, so a script sees it.

The package also names the tests this platform skipped among the test files the
unit touched (`pytest -q -rs` on those files): a test skipped on Windows runs on
the Linux CI, and the other way round. A POSIX-only bug of the lazy-mcp split
passed on Windows exactly this way, and the reviewer now sees "non verificato su
Linux" next to the gate table instead of a green run that proved nothing.

Globs are shell-style (`fnmatch`), so `docs/*.md` and `03-INFRA/**` both work.

## Visible windows

`supervision_windows` opens a terminal that runs a command and stays open; the
engine never reads its output, and a window that cannot be opened raises
instead of falling back to a hidden process.

- Windows: **one** Windows Terminal window called `nexgen-sup`, and one tab per
  session (`wt -w nexgen-sup new-tab ...`). Ten units are ten tabs, not ten
  windows. A `;` in the command is escaped as `\;`, because Windows Terminal
  chains its own subcommands on it. Without Windows Terminal the fallback is a
  dedicated console (`CREATE_NEW_CONSOLE`), which has no tabs.
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

**Subagents are counted.** `opencode session export` and `session list` do not
expose the sessions a subagent opened: `session list` lists top-level sessions
only and the export's `info` has no parent field, so the child ids appear only
inside the message text. The harness keeps them in its own store
(`~/.local/share/opencode/opencode.db`, column `parent_id`, in the `session` and
`session_v2` tables), and that store is what the engine reads, read-only, depth
first: the row's `cost_usd` is the session plus every session under it, with
`children`, `children_cost_usd` and `children_source` naming the parts. When the
store is missing or unreadable the row says `figlie non esposte` and keeps the
parent's cost: an unread source is never written as "no children".

A source that does not answer still leaves a row, with `cost_usd: null` and a
`note` naming the reason. A missing measurement is never written as zero, and
`sup costi` says how many rows had no source.

## What these phases do not do

- No review loop and no approval: the supervisor reads the package and decides
  outside the engine. That cycle, and `sup publish`, belong to phase 3.
- No LangGraph, no resume: the state is files, so a restart reads them.
- No per-role permission profile: an executor runs with the owner's OpenCode
  configuration, which is why the engine never adds an auto-approval flag.
- Only the OpenCode harness is measured. The scout (`agy`) and the supervisor
  CLI stay unmeasured and would be recorded as unavailable.
- No cleanup of old task folders or unit copies: the retention decision (30
  days) belongs to the same rule as research sessions and is not implemented.
