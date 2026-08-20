# Archived: Round 2 review fixes (2026-08-20)

This file used to be a live verification log for six defects (R1–R6) found in
an earlier review round, with file:line citations against the codebase as it
stood on 2026-08-20. The codebase has moved since — module paths this file
cited (for example the `vault-groom` launcher, which it described as
delegating to `vault_groom_audit.py`) have been reorganized, and the exact
test count it recorded is no longer the current one. Keeping those specifics
here would just be a second copy of the code that goes stale on its own, so
this file is retired to a short summary instead.

All six defects it tracked are still fixed in the current codebase, verified
directly, not by trusting the old log:

- **R1 — automatic fast-forward on BEHIND**: `git_ops.py` still defines
  `GitState.BEHIND` as an apply-allowed state and a `fast_forward_merge`
  helper; the guard run still applies it.
- **R2 — `updater.py` shebang and executable bit**: still present.
- **R3 — Council has a real CLI subcommand**: `nexgen council` is registered
  in `nexgen_core/cli/tool_cmds.py`.
- **R4 — `vault-groom` no longer crashes**: `nexgen vault groom` (and the
  legacy `vault-groom` alias) now runs a real preview/apply flow, implemented
  in `nexgen_core/vault/groom.py`. See `docs/what-gets-written.md` and the
  `vault-groom` skill for current behavior.
- **R5 — `mcp-remote` version pin**: `renderer.py` still pins a specific
  `mcp-remote` release.
- **R6 — skill GitHub origin error handling**: `skills.py` still reports
  clone/checkout failures explicitly instead of swallowing them.

For current, non-archival documentation of any of these areas, read the
relevant module or `docs/what-gets-written.md` rather than this file.
