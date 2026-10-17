# Engineering rules

Rules marked **[gate]** are checked mechanically by `python 03-INFRA/scripts/discipline.py` (run by `check_engine.py` and CI).
The rest rely on you. Do not argue with a gate: change the work.

1. **Reuse first, smallest diff.** Search the codebase (`rg`) before writing and name what you reuse.
   One goal per change. No drive-by refactors of code you were not asked to touch.
2. **Propose first, in plain words, and wait for the owner's yes** before adding a dependency, a script
   language, a data-format or public-interface change, a new top-level package, or a change to CI, hooks or
   discipline config. In an unattended session, write the proposal down and do not do it. New files inside
   an existing module need no permission.
3. **One component, one job, explained.** A file owns one behavior and a function does one step of it.
   Each file opens with a docstring that says its one job and why it exists, and each function says the step it
   does, so an agent can understand the component from its header without reading the callers. Line counts are
   the smoke alarm, not the rule: past 500 lines a file (tests exempt) and past 60 lines a function join the
   **split queue** that `discipline.py report` prints and `audit` lists in full. The queue is work to do when a
   file is next touched; it never blocks a push. When a change does not fit, **split first**: extract a component
   with its own job (behavior pinned by tests before the code moves), then make the change in the smaller file.
   **Never compact existing code to fit** (joining lines, shortening messages, squeezing calls): that hides the
   alarm instead of answering it. A file that really does one thing says so at the top with
   `nexgen: one-job reason="what its one job is"` and stays out of the queue up to 1000 lines.
4. **[gate] Logic goes in the main language.** Shell and PowerShell are thin launchers: under 40 lines,
   no parsing, no state, no retries. Anything bigger needs `nexgen: allow reason="..."` at the top of the
   file, with a real reason.
5. **Keep it simple and loud.** No abstraction, option, flag or wrapper for a single use; delete the code you
   replace. No secrets in error messages. **[gate]** No new `except` that swallows the error (`pass`, `return
   None/False`): raise, record the failure, or explain on the `except` line with `# nexgen: allow reason="..."`.
6. **Test the behavior.** A bug gets a failing test first; a feature gets a test of what it does, including the
   failure path. Expected values come from a known literal or the spec, never recomputed the way the code does.
   **[gate, per commit]** A commit that changes critical code (`review_paths`) carries a test in the same commit. A commit
   titled `fix...:` must also show its test failing on the code before the fix: the gate runs it against the parent
   (`test_command`, default `python -m pytest -q`) and refuses a test that passes either way. If no test is possible, say so in
   the commit message: `nexgen: no-test reason="..."`. The `fix` title is the trigger for the proof (an honesty check, like receipts); critical code needs its test whatever the title. **[gate]** Tests are never deleted, reduced in number or assertions, or skipped without the
   owner adding the path to `test_removal_ok` in `.nexgen-discipline.toml` first. Every project defines one
   real-flow command (`smoke_command`) that proves the program works; until it exists the health report is red.
   "Done" means the checks ran and their output is shown. Say what you did not verify.
7. **[gate] Paid reviews are the exception.** They cost the owner real money, so by default there is none: tests and
   the automatic gates are enough. A review or the owner's recorded decision is required only for (a) a bigger
   change (score 4 or more: about 200 net lines, or a new file plus about 100 lines) to the project's **critical files**, the short
   `review_paths` list in `.nexgen-discipline.toml` where a mistake loses user data, opens a security hole or breaks
   updating, and (b) any change to the discipline config or to the CI that runs the gate. The owner can ask for a
   review at any time (`python 03-INFRA/scripts/discipline.py review`, then `receipt`), or settle it for free by recording their own decision:
   `python 03-INFRA/scripts/discipline.py approve --reason "<the owner's words>"`. **Agents never run `approve` on their own**: only after the owner
   has said so in this session, quoting what the owner said. Any edit after `review` or `approve` invalidates it.
   Receipts are an honesty check, not a security boundary: read `.nexgen-discipline/receipts/*.json` to see who
   decided what. CI judges only the push it is given, so run the gate before you push; `check_engine.py` does.
8. **Never bypass a gate.** No `--no-verify`, no raised limits (they are read from the base revision), no
   skipped or deleted tests. If a gate is wrong, tell the owner and stop.

## Starting or checking any project

- New project: `python 03-INFRA/scripts/discipline.py init --smoke "<command that proves it works>"` creates the config, a `DECISIONS.md` for the
  owner's decisions, and local git hooks that run the checks (and fail loudly if the engine is missing).
- `python 03-INFRA/scripts/discipline.py report` prints a short health report in plain Italian for the owner (green, yellow or red) with the split queue.
- `python 03-INFRA/scripts/discipline.py audit` lists the whole split queue and the shell debt. Neither ever refuses a push: they are work to do, not a gate.
