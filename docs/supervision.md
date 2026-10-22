# Supervised tasks (phases 1, 2, 3a, 3b and 3c)

A supervised job is one objective carried out by harness agents that the owner
can watch: a frontier model writes the brief, an executor works in a window of
its own harness, and the engine keeps the files, the money and the verdict.
This document covers **phases 1, 2, 3a, 3b and 3c**: the task folder, the visible
windows, the cost register, the unit's working copy, the permissions the
executor runs under, the gates the engine runs before anybody reviews a diff,
the review round, publication, the resume graph, the scout, the rebase, the
harness adapters, the local executor and the retirement of a finished unit.

**Status: preview.** The verbs work and are covered by tests, but the
supervisor still drives every step. See "What the gates guarantee, and what they
do not" below before relying on a PASS.

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
`unit_queued`, `attached`, `harness_session_lost`, `harness_session_foreign`,
`unit_reworked`, `unit_model_refused`, `unit_stalled`, `unit_resumed`,
`unit_rebased`, `unit_collected`, `report_relocated`, `unit_reviewed`,
`unit_published`, `unit_retired`, `unit_retire_failed`,
`local_server_started`, `local_server_stopped`, and for the scout
`scout_opened` and `scout_finished`. The Markdown files are what a person reads
and writes; the engine never rewrites them, so an edit is not lost.


## The verbs

```bash
nexgen-local sup start "porta la fase 2 dentro il motore"   # id and folder
nexgen-local sup status                                     # every task, one line each
nexgen-local sup status <id>                                # units, states, verdicts, costs
nexgen-local sup unit <id> <unit> --repo <checkout> --base <ref> --brief <file> \
    --allow 'docs/*.md' [--allow ...] [--verify "<command>"]
nexgen-local sup run <id> <unit> [--harness <name>]         # the executor's window
nexgen-local sup collect <id> <unit>                        # the engine's gates, then the cost, then its tab closes
nexgen-local sup review <id> <unit> --approve "<why>" [--close]  # one review round, APPROVA
nexgen-local sup review <id> <unit> --rework <file>         # one review round, RIMANDA
nexgen-local sup review <id> <unit> --resend [-m <model>]        # deliver the last round that never arrived
nexgen-local sup publish <id> <unit> --repo <checkout> --branch <branch> \
    [--remote origin] [--push | --push-with "<command>"]
nexgen-local sup attach <id> <unit> --role esecutore --session <ses_...> [--harness <name>]
nexgen-local sup costi <id>                                 # total, then by role and by unit
nexgen-local sup costi                                     # the 5h, 7d and 30d windows
nexgen-local sup permessi <id>                              # what the session waits on
nexgen-local sup permessi <id> --per-profilo                # refuse everything waiting
nexgen-local sup permessi <id> --reject <request> [--allow <request> | --always <request>]
nexgen-local sup scout <id> "<question>" [--harness <name>]  # the explorer, on a detached copy
nexgen-local sup rebase <id> <unit>                          # realign on the base's current tip
nexgen-local sup resume <id> [--repo <checkout> --branch <branch>] [--riparti]
nexgen-local sup pulizia [--chiudi]                          # ciò che il motore ha lasciato aperto
nexgen-local sup window-check                               # opens a visible window and leaves it open
```

`sup attach` writes the journal event **and** one row in `05-costi.jsonl`.
`sup status` and `sup costi` never compute a price of their own: they read what
the harness reported.

## Chi fa cosa

**The supervisor** is whoever can run the `sup` verbs: a person, a Claude Code
session, an OpenCode session with its own window. The engine does not care which
one it is, because the supervisor's authority is the verbs and the gates, not the
model that types them. Every step the supervisor takes is a file it writes or a
command it runs, so a later reader can reconstruct the decision.

**The executors and the scout are adapters**, one module per harness under
`supervision_adapters/`. The engine never names a harness in its orchestration:
it asks the registry, and the adapter declares what it can do. An operation a
harness does not support is a capability set to false, and the engine refuses
with that name instead of pretending. A role is configured once in the lane,
`supervision.roles`, and a single run may override it with `--harness`:

```json
{
  "supervision": {
    "roles": {"esecutore": "opencode", "ricognitore": "agy"}
  }
}
```

Those two values are the defaults when the key is absent. A name nobody
registered is refused before a tab opens, naming the typo and the names there
are. `sup run --harness <name>` and `sup scout --harness <name>` use that harness
for that run only; the journal records which one actually ran the unit, so the
review delivery goes back to the same session.

| harness | opens a tab | finds the session | sends a turn | state | cost | tokens | writes permissions | resume | scout | frees a folder | stops a push |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `opencode` | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes | project profile + blocked pushurl |
| `local` (OpenCode + local provider) | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes | project profile + blocked pushurl |
| `claude` | yes | yes | yes | yes | yes | yes | yes (`.claude/settings.json`) | yes | yes | no | project profile + blocked pushurl |
| `codex` | yes | yes | yes | yes | no (said unreadable) | yes | no (launch sandbox) | yes | yes | no | `workspace-write` without network + blocked pushurl |
| `agy` | yes | no | no | no | no | no | no | yes | yes | no | blocked pushurl only |

The table is the answer to "can this harness do it", not a promise that every
operation is useful. `agy` opens a tab and writes its report, but it cannot be
asked for its session later, so it is a scout and not an executor; Codex can
execute and resume, but its cost is not exposed in a form the engine reads, and
the package says so instead of writing zero. The last column is the real stop
behind the others: a harness with no project profile is still running in a copy
whose `pushurl` points at a blocked URL.


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

The copy is not meant to outlive its unit: publication and an approved unit
closed without publication both remove it, and `sup pulizia` lists any copy or
branch the journal already closed and the disk still has. "The end of a unit's
work" below is the full rule; the manual `git worktree remove` is now the
recovery step for a copy a live process is holding.

## The executor's window

One harness means one session per task, and the session belongs to the copy it
was born in: the engine reuses it only while the recorded folder is that unit's
copy.

`sup run` looks for the session the task already owns. If there is one and it
is idle (`time.idle` at or past `time.updated` in the harness's own export),
the unit's work goes into it with `opencode run -s <session> "<pointer>"` and
no new tab is opened. If it is busy, the unit is **queued**: the journal records
`unit_queued` with the reason, `sup status` shows `in coda`, and the engine says
so instead of opening another window. A tab is opened when the task has no
session for that harness yet, or when the recorded session belongs to another
unit's copy:

```
opencode <worktree> --prompt "Sei l'esecutore dell'unità ... leggi il brief in ..."
```

The prompt is a pointer to `20-brief-<unit>.md`, never the brief itself, and no
`--auto` or `--dangerously-*` flag is ever passed: the model stays what the owner
configured, and the permissions are the profile described in the next section,
written before the tab opens. The tab's command line is the adapter's: another
harness opens its own window with its own flags, and `sup run --harness <name>`
chooses one for this run without changing the lane's default.

The session that a new tab opens is found in `opencode session list --format
json`, which reports `id`, `created` and `directory`: the newest session whose
`directory` is the unit's copy and whose `created` is at or after the moment the
tab was opened. When no session matches within the wait (30 seconds), `sup run`
says so and the attach stays a manual `sup attach`: the engine never guesses
which session belongs to which unit. The cost of the attached session lands in
`05-costi.jsonl` like any other attach. The adapter owns the lookup: OpenCode
lists its session store, Claude Code reads its transcript directory, Codex reads
its thread store, and `agy` declares that it cannot be asked.

The journal records the folder with the session (`attached`, `unit_started`,
`unit_queued`, `unit_reworked`). `sup run` reuses a session only when that folder
is the unit's own copy; when it is another unit's copy, the engine writes
`harness_session_foreign` with both paths, opens a new tab in the right copy and
says `sessione nata in un'altra copia`. This is the 2026-10-21 failure: the h5
run inherited h4's idle session, whose folder had already been removed, and the
executor saw "working directory no longer exists". A journal row written before
the folder was recorded has no folder, and stays reusable as before.

A session the harness reports as gone (the export says it does not exist) is
not a busy session: queueing on it would wait on nothing for ever. The journal
records `harness_session_lost` with the session and the reason, that session
stops being the task's session, and the next `sup run` or review round opens
the one new tab. A tab is still opened once per harness, never per attempt.

A timeout, a locked store or any other failed read is not the same fact: the
engine tries the export once more, and if it still cannot read the session it
says `sessione non verificabile, riprova` and opens nothing. Treating a
transient failure as a lost session left two sessions running on the same work.

## The executor's permissions

The executor runs under a profile the engine writes before any session starts,
because the profile is decided in advance rather than granted request by
request. OpenCode 2 has no configuration environment variable: a project
`opencode.json` in the working directory is the mechanism, so `sup run` and
every review delivery write that file **inside the unit's copy**, keep every
other key the owner put there (the model stays the owner's), and add the file to
the copy's own `info/exclude`. A project that tracks `opencode.json` is refused
instead of overwritten: the profile must not appear in a diff.

The rules are an ordered list, and the last match wins. OpenCode matches an edit
**inside** the session's folder by its relative path (`docs/a.md`) and one
**outside** it by its canonical absolute path (`C:/.../fuori/vietato.txt`), and
`*` matches any run of characters, separators included. So the copy is the
relative form, every absolute form is closed by its drive letter, the `..` form
is closed for a path that leaves the folder but stays in the same project, and
the task folder is reopened last because that is where the report goes:

| action | resource | effect |
|---|---|---|
| `edit` | `*` | allow |
| `edit` | `?:/*` | deny |
| `edit` | `/*` | deny |
| `edit` | `../*` | deny |
| `edit` | `<task>/*` | allow |
| `shell` | `git push *` | deny |
| `shell` | `git -C * push *` | deny |
| `shell` | `git -c * push *` | deny |

Edits are allowed in the copy and in the task folder, and an edit outside them is
refused. Commands are allowed except a push in its three spellings, and nothing
is auto-approved: a request the profile did not pre-answer is not granted by the
engine. The copy's blocked `pushurl` remains the hard stop behind this one,
because a shell carries the user's authority.

A refused push reaches the model as `Permission denied: shell`, not as a git
error: the tool call never runs, so the session must report it as a permission
denial rather than as a rejected repository.

`opencode.json` goes into the copy's `info/exclude`, which in a worktree is the
**common** file of the repository: the `/opencode.json` line therefore applies in
the owner's checkout too, and hides that name there as well when the file exists.

The other harnesses get the same decision in their own form, and none of them
gets a blanket bypass:

- **Claude Code** reads a project `.claude/settings.json`, written in the copy
  and excluded with the same `info/exclude` line. It sets `defaultMode:
  acceptEdits`, allows `Edit`, `Write`, `Read` and `Bash(git *)`, denies the
  three push spellings (`Bash(git push *)`, `Bash(git -C * push *)`,
  `Bash(git -c * push *)`), and adds the task folder to
  `additionalDirectories` so the report can be written where it belongs.
  `bypassPermissions` is never used.
- **Codex** is started with `-s workspace-write -a never`: writes are confined to
  the workspace and no approval prompt can turn into a yes. Its ability to deny a
  push is declared as weaker than OpenCode's: the sandbox has no network, so a
  push fails, but the copy's blocked `pushurl` is the stop that does not depend on
  the harness.
- **agy** has no project permission profile the engine can write. It is a scout,
  its plan mode is read-only, and the copy's blocked `pushurl` is the only stop.

A harness that cannot deny a push says so in the capability table instead of
being described as safe. `sup run` writes the profile before the tab opens and
refuses when the profile cannot be written, because an executor without its
profile is not the executor the owner configured.

`sup permessi <id>` lists what the task's session is waiting on
(`opencode api session.permission.list`) without changing anything. A request
can be answered one at a time with `--allow`, `--always` or `--reject`, or all at
once with `--per-profilo`, which refuses everything: the profile already decided,
so a question that still appears is outside it. Every answer goes into the
journal as `permission_answered` with its decision.

## The local executor

A unit can run on a model served locally by `llama-server`, through the OpenCode
adapter with the provider the lane configured. The engine owns the server's
lifecycle, because "start it by hand first" is the step that gets forgotten:

- `sup run` starts the server when `/health` does not answer, using
  `supervision.local_model.command`, after checking free RAM (and VRAM when the
  lane declares a threshold). The engine records that it started the server in
  the lane's `local-model-server.json` and in the journal.
- `sup collect` stops a server the engine started, unless another unit of the
  lane still needs it. A server the owner started by hand is left alone: the
  engine only stops what it started.
- A machine that cannot answer the memory question is refused before the model is
  loaded. An unreadable threshold is not a free machine.

A local unit's brief is a **step-by-step plan**, not a goal: the model executes,
it does not design. The brief names each file and each edit, and it carries the
acceptance tests already written and **red**. The unit's boundary excludes the
test files, so the executor can only change production code until those tests
turn green. This is the "test di un altro, codice del locale" scheme:

| who | writes | the local executor may change it |
|---|---|---|
| the supervisor or a frontier model | the brief, the acceptance tests | no (outside `--allow`) |
| the local model | the production code | yes, inside `--allow` |

The scheme exists because of a measured run on 2026-10-21: the local model wrote
a small production fix well, but its own tests were worthless (string searches in
the source, or not compiling) and its report claimed "100 passed" without running
them. Tests written by somebody else and red before the unit starts are the
cheapest way to make the claim checkable: `sup collect` runs the same tests, and
a green run is a fact, not a sentence.

The model is named in the lane's `local_model.model`; `sup run --harness local`
selects the local adapter for one unit, and the journal records the harness, so
the review delivery returns to the same session.

## Stalled sessions

A session that is active and has not moved is the other way an executor
disappears. `sup status` asks the harness for each unit still in progress, and
calls it stalled when the session's `time.updated` is older than the lane's
threshold **and** the unit's last journal row is older by the same amount: a
session whose clock disagrees with this machine is not accused on that alone. A
session the harness reports as stopped is finished, not stalled, and a session
nobody can measure is left alone.

The threshold is `supervision.stall_minutes`, twenty minutes when unwritten. One
`unit_stalled` row is written per stillness, keyed by unit, session and the
session's own last update, so asking again writes nothing; a session that moves
and stalls again gets a new row, and a unit that comes back to work gets
`unit_resumed`. The line reads:

```
unità <unit>: in stallo da 24.3 minuti (soglia 20), possibile richiesta di permesso o turno appeso
```

## The lane's settings

The lane reads `~/.local/state/nexgen/local-lane/config.json` (under the
engine's home when `NEXGEN_HOME` is set). Three groups are read today, all
optional:

```json
{
  "supervision": {
    "allowed_models": ["provider/*", "provider/model"],
    "stall_minutes": 20,
    "roles": {"esecutore": "opencode", "ricognitore": "agy"},
    "local_model": {
      "model": "provider/model",
      "command": ["llama-server", "-m", "<modello>", "--port", "8080"],
      "port": 8080,
      "health": "http://127.0.0.1:8080/health",
      "ram_free_gb": 2.0,
      "vram_free_gb": 0.0
    }
  }
}
```

`roles` is described under "Chi fa cosa". `local_model` is the server a `local`
unit runs on: `command` is the only field without a default, `port` builds the
health URL when `health` is absent, and the two thresholds are the free memory the
engine requires before starting it. When `vram_free_gb` is zero the VRAM check is
skipped, because a machine with no usable `nvidia-smi` is not a machine with no
GPU. A threshold that cannot be read is a refusal, never a pass.

An empty or absent `allowed_models` means no restriction. A list that exists is
matched against the tag the harness reports, `provider/model#variant`, with
shell-style globs, so `provider/*` covers every model of that provider and every
variant of each. With a list, `sup run` refuses a session outside it before
anything is sent (`unit_model_refused`, naming the model) and `sup collect` fails
the `modello consentito` gate on the same fact. A session nobody attached, or one
whose model cannot be read, is not on a list that exists: "not verified" is not
"allowed".

## The engine's gates

`sup collect` judges the unit's `base..head` itself. The executor's report is
not evidence; every line below is read from git or run by the engine.

| gate | fails when |
|---|---|
| `confine` | a touched file (both names of a rename) is outside every `--allow` glob |
| `file vietati` | `VERSION`, `CHANGELOG.md` or `.github/**` changed and the boundary does not name it; both names of a rename count |
| `integrità dei test` | an existing test was deleted, shrunk, skipped or renamed out of discovery (`nexgen_core/discipline/integrity.py`, reused) |
| `prova per commit` | a `fix:` commit has no test that fails on its parent (`nexgen_core/discipline/regress.py`, reused) |
| `verifica indipendente` | the declared command is missing or exits non-zero; it is one command with no shell operator (`&&`, `\|\|`, `\|`, `;`, `&`), its executable must come from outside the unit's copy, and a Python interpreter is started with `-P` and an environment cleaned of `PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONHOME`, `PYTHONUSERBASE`, `PYTEST_ADDOPTS` and `PYTEST_PLUGINS` (the user site directory is left alone: it is outside the copy) |
| `test raccolti` | pytest collects fewer tests on the head than on the base, or a collection cannot answer: both trees are exported with `git archive` (no `.git` in them), collected with `python -P -m pytest --collect-only -q` under the same cleaned environment, and each collection has a five-minute budget; an increase is normal |
| `lint` | the project has `03-INFRA/scripts/ruff_baseline_check.py` and the copy adds a `(file, code)` finding group its committed baseline did not have; the copy only decides whether the gate exists and what its baseline allows, while the script that runs is the engine's own, so a unit cannot weaken the check |
| `configurazione git` | either configuration file of the copy (the repository's shared `.git/config`, which every checkout reads, or the worktree's own `config.worktree`) changed since the unit was created, or the effective configuration carries a `url.*.insteadOf` / `url.*.pushInsteadOf` key the creation did not have; the file or the key is named |
| `remoto intatto` | a remote of the creation is gone, added, points at another fetch URL or push URL, or one of its refs carries a commit of the unit's interval (the unit pushed); a branch that advanced for another reason is not a failure |
| `albero pulito` | the unit's copy has uncommitted work |
| `scansione fughe` | the project declares CI's anti-leak scan (a workflow names `leak_scan.py`) and the scan exits non-zero on the unit's `base..head`, or the script and its patterns are not where the workflow says; a project that declares no scan adds no line |

The verdict is PASS only when every line is. The package
(`40-pacchetto-<unit>.md`) carries the verdict, the gate lines, the diffstat,
the diff with a declared cap (400 lines) and the cost of the attached session;
`50-gate-<unit>.txt` carries the raw output, including what the verification
printed. `sup collect` exits 1 on FAIL, so a script sees it.

The leak line is the same scan CI runs, on the same interval, moved to the point
where the executor can still fix it: a personal name in a fake path or a
seven-digit number in a test used to stop the push, after the review had already
said yes.

Before judging, `sup collect` looks for a `30-resoconto-<unit>.md` left **inside
the copy**. An untracked report is moved to the task folder and the move is said
in the output and in the journal (`report_relocated`); this is the local
executor's habit the 2026-10-21 run exposed, where the report stayed an untracked
file, failed `albero pulito`, and never reached the reviewer. A report committed
to the branch is part of the unit's diff and is not moved: the gates must judge
what it says.

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
pytest` would import a `pytest.py` the unit dropped in the copy. `-P` does not
touch `PYTHONPATH`, so the environment is cleaned as well, and the copy decides
nothing about what the interpreter imports.

The collected count is the answer to a different question than the AST: the
integrity gate counts the test functions a diff still contains, and a
`__test__ = False`, a folder pytest does not walk, a `collect_ignore` or a
`conftest.py` that exits leaves those functions exactly where they were. The
gate runs pytest itself, on two exported trees, and compares the numbers.

## What the gates guarantee, and what they do not

The gates are built to catch mistakes and shortcuts, not to defeat an executor
who is deliberately hostile and has a shell. Every check above reads a fact the
unit cannot quietly rewrite, and every refusal names what it saw. That covers
the ordinary failure mode of an agent under pressure: a deleted test, a test
renamed out of collection, a weakened assertion, a verification that proves
nothing, a push that left the copy, a git configuration edited behind the
owner's back.

It does not cover an executor who is willing to lie in code the engine must run.
Collecting a tree imports its `conftest.py` and its test modules, and the
declared verification runs the unit's own code: code written to print a fake
count, to exit before the truth and then clean up, or to `git push <url>` in the
middle of a run is a determined forger, not an accident. `git push <url>` leaves
no reliable local trace at all, so no gate here can see it; the remote gate reads
the remotes before the verification runs, so a push made by the verification
itself is not what it asks about.

The defence for that layer is outside the gates: the harness permission profile
the executor runs under, which denies `git push` (and the shell spellings that
would reach it), and the supervisor's own review of the diff and the report. The
gates make that review cheap and hard to fool by accident. They do not replace
it, and no further text check added here would.

Residual limits this round leaves open:

- a `url.*.insteadOf` rule is detected by its key, not by its value: changing the
  target of a rule the creation already carried is invisible, and only a new key
  is refused;
- the shared `.git/config` is the owner's too: an owner who edits it while a
  unit exists gets a FAIL naming the file. `sup rebase <id> <unit>` is the
  explicit way to absorb that change: it re-fingerprints the configuration and
  records it in the journal, so the next collection judges the new baseline;
- `--prune` is refused as a token in `--push-with`, but a declared script can
  still run it; the post-push check catches the deletion after the fact, and the
  branch is gone from the remote by then;
- the collection gate compares counts, not identities: a unit that deletes one
  test and adds another keeps the count and passes this gate (the integrity
  gate still refuses the deletion);
- a remote branch that another actor creates between the `before` and the
  `after` read of a publication is reported as a change made by the command.

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

A delivery is not the process: the harness writes its event when `opencode run`
starts, and a provider can end that turn with an error before any work reaches
the copy. The engine reads the session's last turn from the harness's own store
(`session_message`, the `idle` row), and when it failed and is newer than the
delivery, the round stays pending: `sup status` prints

```
consegna fallita: provider.content-filter (giro 1, sessione ses_...): le correzioni non sono arrivate
```

once per failure (`unit_delivery_failed` in the journal), and `--resend` offers
it again. `--resend -m <model>` sends that one delivery to a different model
(`opencode run -s <session> -m <model>`; `run` has no `--variant`), which is the
way out when the provider refuses the configured one. A resend that has to open
a new tab says the model was not applied: the tab takes the model from the
owner's configuration.

`sup status` shows, per unit, the review round, whether it is approved, and the
published head with its branch. It also shows the cost of the unit and of the
task in one form, the same sentence `sup collect` prints:

```
costo: giro 0.0047 $, unità 0.0047 $, compito 0.0047 $ (modello opencode-go/…)
```

The three numbers are measured at the collection: the round is what the session
grew since the round's own delivery started, the unit is what it grew since the
unit's first attach, and the task is the live session plus the sessions that
ended before it. A figure that cannot be read is said in words, never written as
zero.

## The end of a unit's work

When a unit is collected the engine does the two things a finished worker owes:
it says what the work cost and it gets out of the way. `sup collect` measures
the session through the harness (children included), writes the same line into
the journal (`unit_collected` carries the numbers), into `40-pacchetto-<unit>.md`
and into its own output, and then closes the executor's tab with the rules of
the next section: never while the session is still writing, never while another
unit of the task can still work, never a tree that holds the shared server.

After that closure a review must not run without a window. `sup review --rework`
and `--resend` check the journal: when the engine's own last word on the tab is
`tab_closed`, the corrections go into a visible tab opened on the session the
task already has (`opencode <copy> -s <session> --prompt "<pointer>"`, OpenCode
2's own resume flag), not into a hidden `opencode run`. When the tab is still
open the delivery stays off the screen, as before. A model named with
`--resend -m` cannot travel into a tab, and the verb says it was not applied.

Retirement is the owner's rule "a fine lavoro non resta niente", and it runs
only when the work is somewhere permanent:

- **`sup publish`** retires the unit after the commits are verified on the
  owner's branch, including the path where the push failed and the commits stayed
  local.
- **`sup review <id> <unit> --approve "<why>" --close`** retires an approved unit
  that will not be published. Before the branch goes, the engine writes the head
  under `refs/nexgen/archive/<task>/<unit>`, so the history stays reachable; a
  unit that is already published is refused, because `sup publish` knows which
  commits arrived.

The sequence is: ask the unit's adapter to free what the shared server holds for
that folder (`dispose_location`; OpenCode's `debug.location.evict`), then
`git worktree remove --force` with `core.longpaths=true`, then `git branch -D`.
The copy goes before the branch, and a copy a live process still holds stops the
retirement with the last line of git's own error. Nothing is deleted when the
commits cannot be found in any known repository, or when an unpublished unit has
no archive: the refusal names the unit and says which command closes it. The
task folder with its Markdown files and journal is never removed: it is the
record, not the workspace.

A retirement that fails after a successful publication does not undo the
publication: the journal records `unit_retire_failed`, the verb prints the
reason, and `sup pulizia` offers the copy again. A published unit keeps its state
`pubblicata` (or `pubblicata in locale` when nothing was pushed); the retirement
is a cleanup fact, not a different outcome.


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

Once the commits are applied, `sup publish` closes the executor's tab: the
engine wrote a marker into that tab's shell (`NEXGEN_SUP_TAB`), finds the shell
by the exact assignment it wrote, and stops it and its children from the deepest
one up to the shell, so the tab disappears and nothing behind it keeps running.
Then it retires the unit as described above: the copy and the `sup/*` branch go
once the commits are verified. Two things stop the tab closure.
another unit of the same task is created, queued, running, stalled or waiting
for a decision, the tab stays, `tab_left_open` names the open units, and the verb
says why: it closes when the last unit is approved and published. And it refuses
when the tree holds an `opencode serve`: the first tab of the day starts the
server every other session shares, and closing that one would stop every other
worker. Then the tab stays, `tab_left_open` records the pid that made it unsafe,
and the verb says so; when it closes, `tab_closed` records the pids. A tab the
owner already closed is not an error.

## Resuming a task

`sup resume <id>` walks a task through the steps the owner already runs by hand:
`unità -> esecuzione -> raccolta -> revisione -> pubblicazione`. Every node calls
the function the matching verb calls, so there is no second implementation of a
step. The checkpoints live in `60-ripresa.sqlite` in the task folder (LangGraph's
`SqliteSaver`), one per unit: the thread is the task and the unit, and
`61-ripresa.json` keeps one identity record per unit with the SHA-256 of the
task's objective and of the unit's brief.

The run stops where a person is needed and says so:

| stop | meaning |
|---|---|
| `waiting_review` | collected PASS, waiting for `sup review` |
| `waiting_session` | the task's session was busy: the unit is queued |
| `rework` | the review sent corrections into the executor's round |
| `waiting_publish` | approved, but no `--repo`/`--branch` named the owner's checkout |
| `done` | the publication ran |

A resume re-enters the step that stopped, not the one after it, so a wait is
looked at again instead of skipped; a collection already done on the same head
is not repeated, a session already attached is not sent to, and a finished run
reruns nothing. The identity of a run is the task, the unit and the brief's
hash, and it is checked before any step: a brief edited since the run started
refuses with its own reason, because continuing a run whose instructions changed
is worse than stopping. A different unit simply has its own checkpoint in the
same file, so two units of one task resume independently. `--riparti` throws
away the checkpoint of the unit it names and nothing else.

The publication node does nothing unless `--repo` and `--branch` name the
owner's checkout: approval alone stops at `waiting_publish` and prints the
command that remains. With both, it calls the same `sup publish`.

## Realigning a unit

`sup rebase <id> <unit>` puts the unit's branch back on the current tip of its
base and records the new base in the journal, so `sup collect` and `sup publish`
judge the right interval. On a base that advanced during the unit, the old base
made collect see commits already on the remote as the unit's own; this command
is the fix.

It is a local rebase: the tip is read from the owner's checkout, and the engine
fetches nothing. A base that was rewritten (the old base is no longer an ancestor
of its new tip) is refused instead of replayed, because replaying it would
publish history nobody reviewed. A published unit is refused: realigning it would
rewrite commits the owner already has. Uncommitted work in the copy is refused
before anything moves.

A conflict runs `git rebase --abort`, leaves the copy exactly as it was, and
names the files: a copy left half-rebased is a trap for whoever collects it
next. On success the journal gains `unit_rebased` with the previous base, the new
base, the head, and the refreshed fingerprint of `.git/config` and
`config.worktree`. That fingerprint is also why the command exists beside the
rebase: a legitimate change the owner made to the configuration is absorbed by
one explicit command instead of forcing a new unit. A branch already on the new
tip just records it, and a command that moves neither the base nor the
configuration writes no row at all.

## The scout

`sup scout <id> "<question>" [--harness <name>]` gives the explorer (`agy` by
default, from `supervision.roles.ricognitore`) a throwaway copy of the repository
the task's first unit works on, at a fixed path: `<task>/24-ricognizione/`. The
folder is recreated at every round with a plain clone and `checkout --detach
<base>`, so the scout never inherits yesterday's edits and holds no branch whose
work could be mistaken for the executor's.

Before the tab opens, that exact path is added to `trustedWorkspaces` in
`~/.gemini/antigravity-cli/settings.json` when it is not already there
(atomic write, `.bak` kept, only that key touched). The path never changes, so
one trust entry covers every round.

The question goes into `25-brief-ricognizione.md`; the report the scout must
write is `26-rapporto-ricognizione-<round>.md`, outside the copy. The tab runs
this same engine in its scout role, which starts the adapter's scout command: by
default `agy --mode plan --prompt <pointer>`, and another harness supplies its
own read-only command (`opencode run --agent plan`). Plan mode is read-only, and
the pointer keeps the brief in one file.
At the end of the round the engine asks git whether the copy is where it started
(`git status --porcelain`, unchanged HEAD), then closes the tab it opened. A
tracked file that changed, or a moved head, ends the round with exit 1 and a
`scout_finished` row that lists what changed. An untracked file (a cache, a lock
a tool left behind) is not a change to the code: it is listed in
`scout_finished` as `untracked` and does not fail the round. The code check is
the proof, not the scout's promise.

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
instead of falling back to a hidden process. A tab opened by the engine carries
its marker in one exact place, and only a shell is looked at: on Windows the
assignment in the command line (`$env:NEXGEN_SUP_TAB=nexgen-sup:<task>:<role>`),
on Linux the exported variable in `/proc/<pid>/environ`. A `grep` or an editor
that merely quotes the marker is not a tab and is never touched. The engine
closes exactly that tab when the work is done: after `sup publish` for the
executor, when no other unit of the task can still work, at the end of the round
for the scout, and never when the tree holds the shared `opencode serve`.

On Linux the tab is a terminal window whose shell is the tab: the tree is read
from `/proc` (stat, cmdline, environ) and stopped deepest first, and the window
follows its shell. The same rule about the shared server applies, so a machine
where the terminal started `opencode serve` keeps that window.

`sup pulizia` is the verb for what the engine left behind. It lists every marked
tab whose task is finished, has no units, or can no longer be opened by this lane
(a proof in a temporary `NEXGEN_HOME` whose folder was deleted), and with
`--chiudi` closes them with the same deepest-first rule and the same refusal
around the shared server. It also lists, without touching them, the `opencode`
and `agy` processes that no tab holds, and the number of processes hanging under
`opencode serve`: those are the MCP servers of sessions that are already closed,
which is how OpenCode behaves, so the line says whether the service can be
restarted now.

It also answers the rule that nothing is left behind. It lists the copies and
branches of units whose journal says the work arrived (`pubblicata`, `pubblicata
in locale`, `ritirata`, approved and closed) and the disk still has, and
`--chiudi` retires them. With a deep read it asks the shared server which folders
it still holds (`opencode api GET /api/debug/location`): a folder that no longer
exists is listed with the start time of its oldest process, taken from the
process table, and `--chiudi` evicts it through the same API the retirement uses.
A folder that still exists is never in that list, so an active session and the
owner's own server are out of reach. The local-models bench is not the engine's:
no verb stops it except the local unit that started it.

`sup collect` prints the same fact as a note, not a gate: marked tabs of tasks
this lane no longer has stay open after the collection, and that is noise and
memory, not a defect of the unit under review.

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
read/write), cost in dollars, and the source of the measurement. The source is
the unit's own harness, through its adapter: OpenCode and the local executor
through `opencode session export <id>`, Claude Code through its transcript under
`~/.claude/projects/`, Codex through its store. Codex reports tokens and no
money, and `agy` reports neither: the row says the number is unreadable instead
of writing a zero.

`sup costi` without an id shows what the harness archive says was spent in the
last 5 hours, 7 days and 30 days: every session row in `session_v2` (or the old
`session` table), children included, windowed by `time_updated`. The store is
opened read-only, and a store that cannot be read makes the window unknown with
the reason, never zero. **No budget rule lives in the engine:** the numbers are
shown, and what to do about them stays the owner's decision.

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

- No age-based retention of task folders: a unit's copy and branch are removed
  when the unit is retired, but an old task folder and its journal stay until the
  owner decides otherwise. The 30-day retention is the same decision as for
  research sessions and is not implemented; `sup pulizia` is the manual cleanup
  for what a closed unit left on disk.
- Cost is measured only where the harness exposes it. Codex reports tokens and no
  money; `agy` reports neither and cannot be asked for its session after the tab
  opens, which is why it stays a scout. Claude Code's cost comes from its own
  transcript, a file the harness may rotate.
- No tag and no release: `sup publish` never creates one, and a commit touching
  `VERSION` or `CHANGELOG.md` is refused by name. A release stays
  `nexgen release ship` from the owner's checkout.
