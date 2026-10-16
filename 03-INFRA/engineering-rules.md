# Engineering rules

Rules marked **[gate]** are checked mechanically by `python 03-INFRA/scripts/discipline.py` (run by `check_engine.py` and CI).
The rest rely on you. Do not argue with a gate: change the work.

1. **Reuse first, smallest diff.** Search the codebase (`rg`) before writing and name what you reuse.
   One goal per change. No drive-by refactors of code you were not asked to touch.
2. **Propose first, in plain words, and wait for the owner's yes** before adding a dependency, a script
   language, a data-format or public-interface change, a new top-level package, or a change to CI, hooks or
   discipline config. In an unattended session, write the proposal down and do not do it. New files inside
   an existing module need no permission.
3. **[gate] One file, one job.** A file does one thing and a function does one step; line counts are the smoke
   alarm, not the rule. Past 500 lines a file (tests exempt) must not grow unless it declares its single job at the
   top with `nexgen: one-job reason="what its one job is"` (up to 1000 lines); otherwise extract the new code into
   its own module. A function past 60 lines never grows: split it.
4. **[gate] Logic goes in the main language.** Shell and PowerShell are thin launchers: under 40 lines,
   no parsing, no state, no retries. Anything bigger needs `nexgen: allow reason="..."` at the top of the
   file, with a real reason.
5. **Keep it simple and loud.** No abstraction, option, flag or wrapper for a single use; delete the code you
   replace. No secrets in error messages. **[gate]** No new `except` that swallows the error (`pass`, `return
   None/False`): raise, record the failure, or explain on the `except` line with `# nexgen: allow reason="..."`.
6. **Test the behavior.** A bug gets a failing test first; a feature gets a test of what it does, including the
   failure path. **[gate]** Tests are never deleted, reduced in number or assertions, or skipped without the
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
- `python 03-INFRA/scripts/discipline.py report` prints a ten-line health report in plain Italian for the owner (green, yellow or red).
- `python 03-INFRA/scripts/discipline.py audit` lists the existing debt. The gates never fail on debt that already existed, only on making it worse.
