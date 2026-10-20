# Supervised tasks (phases 1, 2 and 3a)

A supervised job is one objective carried out by harness agents that the owner
can watch: a frontier model writes the brief, an executor works in a window of
its own harness, and the engine keeps the files, the money and the verdict.
This document covers **phases 1, 2 and 3a**: the task folder, the visible
windows, the cost register, the unit's working copy, the gates the engine runs
before anybody reviews a diff, the review round, and publication. The LangGraph
checkpoint and its resume belong to phase 3b: the state here is still the
journal, and every step is a function the graph can wrap.

**Status: preview.** The verbs work and are covered by tests, but the
supervisor still drives every step, and phase 3b (spending cap, provider
allow-list, harness permission profile, stall detection, resume) is not in yet.

## What the gates guarantee, and what they do not

The gates catch mistakes and shortcuts: a file outside the boundary, a touched
VERSION or CHANGELOG, a deleted or weakened test, a fix without its failing test,
a verification that does not pass when the engine runs it, a commit of the unit
found on a remote, uncommitted work. They read the unit's commits and rerun its
checks; they are not a sandbox.

An executor that sets out to cheat with a shell of its own can still get past a
reading of the diff, for example by pushing straight to a URL outside the
configured remotes or by changing how pytest collects tests. Against that the
defence is the harness's own permission profile (phase 3b denies the executor
`git push`) and the supervisor's review of the diff. Treat a PASS as "nothing
the engine checks went wrong", not as "this change is safe".

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
own working copy in `23-unita-<unit>/`. A review adds
`45-revisione-<unit>-<round>.md`, and every request the engine refuses to
execute goes to `07-richieste-proprietario.md`.

The journal is the machine truth: `started`, `unit_created`, `unit_started`,
`unit_queued`, `attached`, `harness_session_lost`, `unit_reworked`,
`unit_collected`, `unit_reviewed`, `unit_published`. The Markdown files are what
a person reads and writes; the engine never rewrites them, so an edit is not
lost.


## The verbs

```bash
nexgen-local sup start "porta la fase 2 dentro il motore"   # id and folder
nexgen-local sup status                                     # every task, one line each
nexgen-local sup status <id>                                # units, states, verdicts, costs
nexgen-local sup unit <id> <unit> --repo <checkout> --base <ref> --brief <file> \
    --allow 'docs/*.md' [--allow ...] [--verify "<command>"]
nexgen-local sup run <id> <unit>                            # the executor's window
nexgen-local sup collect <id> <unit>                        # the engine's gates, exit 1 on FAIL
nexgen-local sup review <id> <unit> --approve "<why>"       # one review round, APPROVA
nexgen-local sup review <id> <unit> --rework <file>         # one review round, RIMANDA
nexgen-local sup review <id> <unit> --resend                # deliver the last round that never arrived
nexgen-local sup publish <id> <unit> --repo <checkout> --branch <branch> \
    [--remote origin] [--push | --push-with "<command>"]
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

The fetch URL and the blocked push URL of every remote are written into the
journal when the unit is created, and its refs are read with `git ls-remote` on
that URL. `sup collect` asks three questions: are the creation's remotes still
configured with the same fetch URL and the blocked push URL, did the unit add a
remote of its own, and does any remote ref point at a commit of this unit's
`base..head`? `remote.<name>.pushurl` wins over the fetch URL for every push, so
re-pointing it was a push the gate could not see. A removed, added or re-pointed
remote is a FAIL named after it, a pushed commit is a FAIL named after the ref
that carries it, and a remote branch that advanced for another reason (another
unit was published) is not a failure: the gate looks for this unit's own commits,
not for a world that stood still.

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

A session the harness reports as gone (the export says it does not exist) is
not a busy session: queueing on it would wait on nothing for ever. The journal
records `harness_session_lost` with the session and the reason, that session
stops being the task's session, and the next `sup run` or review round opens
the one new tab. A tab is still opened once per harness, never per attempt.

A timeout, a locked store or any other failed read is not the same fact: the
engine tries the export once more, and if it still cannot read the session it
says `sessione non verificabile, riprova` and opens nothing. Treating a
transient failure as a lost session left two sessions running on the same work.

## The engine's gates

`sup collect` judges the unit's `base..head` itself. The executor's report is
not evidence; every line below is read from git or run by the engine.

| gate | fails when |
|---|---|
| `confine` | a touched file (both names of a rename) is outside every `--allow` glob |
| `file vietati` | `VERSION`, `CHANGELOG.md` or `.github/**` changed and the boundary does not name it; both names of a rename count |
| `integrità dei test` | an existing test was deleted, shrunk, skipped or renamed out of discovery (`nexgen_core/discipline/integrity.py`, reused) |
| `prova per commit` | a `fix:` commit has no test that fails on its parent (`nexgen_core/discipline/regress.py`, reused) |
| `verifica indipendente` | the declared command is missing or exits non-zero; it is one command with no shell operator (`&&`, `\|\|`, `\|`, `;`, `&`), its executable must come from outside the unit's copy, and a Python interpreter is started with `-P` |
| `remoto intatto` | a remote of the creation is gone, added, points at another fetch URL or push URL, or one of its refs carries a commit of the unit's interval (the unit pushed); a branch that advanced for another reason is not a failure |
| `albero pulito` | the unit's copy has uncommitted work |

The verdict is PASS only when every line is. The package
(`40-pacchetto-<unit>.md`) carries the verdict, the gate lines, the diffstat,
the diff with a declared cap (400 lines) and the cost of the attached session;
`50-gate-<unit>.txt` carries the raw output, including what the verification
printed. `sup collect` exits 1 on FAIL, so a script sees it.

The package also names the tests this platform skipped among the test files the
unit touched (`pytest -q -rs` on those files, with `-n 4` when pytest-xdist is
installed and a five-minute ceiling): a test skipped on Windows runs on the
Linux CI, and the other way round. A POSIX-only bug of the lazy-mcp split passed
on Windows exactly this way, and the reviewer now sees "non verificato su Linux"
next to the gate table instead of a green run that proved nothing.

A unit that touches `conftest.py`, `pytest.ini`, `setup.cfg`, `tox.ini` or the
pytest section of `pyproject.toml` is named in the package under "Superficie di
pytest". Those files change how the suite collects and runs; the gate does not
fail over them, the reviewer looks at them before approving.

Globs are shell-style (`fnmatch`), so `docs/*.md` and `03-INFRA/**` both work.

The verification command is one command, not a shell line. The engine never
hands it to a shell, so `&&`, `||`, `|`, `;` and `&` are refused before anything
runs, with the advice to write a script or a single command; a `;` inside a
quoted argument is still one token and is not mistaken for a separator. Its
executable must also come from outside the unit's copy: Windows resolves a bare
name in the working directory before `PATH`, so a `pytest.bat` planted in the
copy would answer in place of the real runner, and any path that resolves inside
the copy is refused with the file named. A Python interpreter is started with
`-P`, which keeps the working directory off `sys.path`: without it `python -m
pytest` would import a `pytest.py` the unit dropped in the copy.

## The review round

`sup review` is the supervisor's turn, and it is a file, not a sentence in a
terminal. Each call writes one round, numbered from 1 and never rewritten:
`45-revisione-<unit>-<round>.md` carries the verdict, the head it judged, the
verdict of the last collection on that head, and either the reason or the
corrections.

`--approve "<reason>"` is accepted only when the engine's last `sup collect`
said PASS **and** the unit's branch still points at the commit that collection
judged. A branch that moved is refused with both hashes, because an approval
must name a commit a person could read. A collection event without a head is
refused too: "rifai `sup collect`", never an approval of whatever commit is
there. The round records the full head, so a later move is visible instead of
inferred.

`--rework <file>` writes a RIMANDA round and sends the corrections into the
executor's own session with the same mechanism as `sup run`: `opencode run -s
<session>` when the session is idle, `unit_queued` with the round when it is
busy, and a refusal when the task has no session at all. The command line
carries a pointer to the review file, never its text. The review round is
written before the delivery: corrections exist even when the session cannot be
reached, and the error says so.

A round number advances only when the previous round was delivered. While a
round is queued or was never sent, `--rework` is refused and says to use
`--resend`: that command delivers the last undelivered round again, into the
session if it is free, into the queue if it is busy, and through the one new
tab when the task still has no session. The round file and its number do not
change.

`sup status` shows, per unit, the review round, whether it is approved, and the
published head with its branch.

## Publishing

`sup publish <id> <unit> --repo <checkout> --branch <branch> [--remote origin]
[--push | --push-with "<command>"]` is the only push in the engine, and it
happens after every refusal that can be decided without touching anything:

| refusal | when |
|---|---|
| `non approvata` | the last review round is not an APPROVA |
| `cambiata dopo l'approvazione` | the unit's branch moved since the approval |
| `checkout inesistente` / `non è un repository git` | `--repo` is not a checkout |
| `copia dell'unità` | `--repo` is the unit's own working copy |
| `il checkout è su …, non su <branch>` | the owner's checkout is elsewhere |
| `checkout sporco` | uncommitted work in the owner's checkout, named |
| `bump di versione` | a commit touches `VERSION` or `CHANGELOG.md`, a rename included, **even inside the unit's boundary** |
| `servirebbe un force-push: il motore non lo fa mai` | the fetched remote branch is not an ancestor of the owner's head |
| `i cancelli del motore, rieseguiti ora, danno FAIL` | the gates fail on the interval, named line by line |
| `conflitto` | a cherry-pick conflicts; the files are named and the checkout goes back |

The order is deliberate: the cheap and decisive refusals come first, and the
gates re-run on the interval before the first cherry-pick, so a failing gate
leaves the owner's checkout exactly as it was. The forward-only question is
asked before the cherry-pick as well, by comparing the fetched remote branch
with the owner's current head, which is the same question for the new head.

Publication replays the unit's commits one at a time, oldest first, with `git
cherry-pick`, so the order is preserved and the signature follows the owner's
own repository configuration: when `commit.gpgsign` is on, the published commits
are signed because the owner's git signs them, never because the engine invents
a key. `sup publish` prints `commit.gpgsign` and the `%G?` mark of every
published commit, so "firmato" is a fact a reviewer reads rather than assumes.
No tag is ever created, and no option of force exists in any code path.

A conflict runs `git cherry-pick --abort` and, if the head still moved, resets
the checkout to the commit publish found there; the refusal names the files in
conflict and says the checkout went back. A unit already pushed at the same
head is refused rather than replayed, because a second replay would double its
commits. A unit applied locally but never pushed (`pushed: false`) is the
exception: `--push` or `--push-with` checks that the branch still contains the
published commits and pushes, without a second cherry-pick. That push sends
exactly the published head (`<head>:refs/heads/<branch>`), so a commit the owner
adds to the branch afterwards stays local: the engine never publishes what it
did not review. A branch rewritten so that it no longer contains the published
head is refused by name. The same command without a push flag refuses with the
flag to add.

Without `--push` or `--push-with`, publish stops after the local cherry-pick and
prints the one command that remains. `--push` runs `git push --no-follow-tags
<remote> <branch>`, so a repository's `push.followTags` cannot send tags.

A push is declared, then checked, never believed. Before the push the engine
reads the remote branch and its tag list; after it, it reads both again and
requires the branch to point exactly at the published head, the value read
before to be an ancestor of the new one, and the tag list to be unchanged.
Anything else is a named failure (`il comando di pubblicazione ha forzato la
storia` or `... ha creato o spinto tag`), the journal keeps `pushed: false` and
the command's own failure is printed.

`--push-with "<command>"` runs the owner's own publishing command with the
engine's launch rules (`nexgen_core.processes`, argv, no shell) inside the
owner's checkout, and the same post-check applies. The declared command is
refused before it runs when it carries a force in any spelling: `--force`,
`-f`, a bundle of short flags containing `f`, `--force-with-lease`,
`--force-with-lease=<ref>`, `--force-if-includes`, `--mirror`, `--delete`, a
refspec starting with `+` (force) or `:` (delete).

The push goes to the remote the checkout already shares history with, which is
what `origin` is. A shallow checkout cannot seed a different, empty remote: git
itself refuses (`Failed to traverse parents`, then `shallow update not allowed`),
and that is a property of the clone, not of the engine.

## What the engine refuses to execute

An executor's report may ask for a force-push, a tag, a version bump or a
change to an existing test, but it may also ask for something the engine never
named. The only source is the request section of the report: a heading that
contains "richieste", with or without "dubbi" (`## Richieste al proprietario`
and `## Dubbi e richieste al proprietario` are the same section). The brief, a
review and a corrections file are the supervisor's words and are never scanned.

Inside that section every list item (`- text` or `1. text`) is a request and
goes to the owner as written; a keyword only classifies it, and an item no
keyword knows is filed as `altro`. An item that says "nessuna richiesta" or
"nessun dubbio" is not a request. Fenced code blocks and quoted lines (`>`) are
ignored, so pasting an output is not a request, and a plain paragraph beside
the list is the report talking, not an ask. With no section nothing is
recognised and the package says `sezione delle richieste assente nel resoconto`.
One line per unit, kind and sentence goes to `07-richieste-proprietario.md`, and
a sentence already on file is not written again, so the file cannot quote
itself back as a new request. `sup collect` prints the lines it wrote, and the
decision stays the owner's.

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

- No LangGraph graph and no `sup resume`: the review and publish steps are plain
  functions and the state is the journal, so a restart reads what happened and
  phase 3b can wrap the same functions without re-deciding anything.
- No per-role permission profile: an executor runs with the owner's OpenCode
  configuration, which is why the engine never adds an auto-approval flag.
- Only the OpenCode harness is measured. The scout (`agy`) and the supervisor
  CLI stay unmeasured and would be recorded as unavailable.
- No cleanup of old task folders or unit copies: the retention decision (30
  days) belongs to the same rule as research sessions and is not implemented.
- No tag and no release: `sup publish` never creates one, and a commit touching
  `VERSION` or `CHANGELOG.md` is refused by name. A release stays
  `nexgen release ship` from the owner's checkout.
