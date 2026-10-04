# Contributing

This is a solo-maintainer project. Contributions are welcome,
but set expectations accordingly: review can take a while, and not every PR
fits the project's scope (see "Design boundaries" and "Scope and
limitations" in `README.md`) even when it's well built.

## Before you write code

For an external contribution bigger than a small fix, open an issue describing the
problem and your proposed approach. This saves you from building something
that doesn't fit the project's direction. For a small, obvious fix (typo,
broken link, clear bug with an obvious one-line fix), a PR alone is fine.

For AI-assisted maintainer work, start with [AGENTS.md](AGENTS.md), then the
owner table below. An authorized maintenance task uses the maintainer's durable
lane and isolated worktree; it does not require a new issue or a per-fix branch.
Read the affected callers and tests before editing. Change the owner, keep
compatibility at the existing boundary, and integrate after verification.

## Dev setup

Use Python 3.11 or newer and install the development dependencies:

```sh
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -e '.[dev]'
python 03-INFRA/scripts/check_engine.py
```

The last command runs the committed Ruff baseline gate and the full pytest
suite. It stops on the first failing gate and never invokes sync or release.
For a bounded change, pass the relevant test file first; run the full command
before integration. Fix new lint findings rather than regenerating the baseline.

### Finding the right module

| Behavior | Owner | Closest tests in `03-INFRA/agent-universal-layer/tests/` |
| --- | --- | --- |
| Atomic writes, backups, private artifacts | `nexgen_core/files.py` | `test_nexgen_foundations.py`, `test_nexgen_quality_regressions.py` |
| Third-party pin updates and rollback | `nexgen_core/thirdparty_bump.py` | `test_nexgen_bump.py`, `test_nexgen_bump_file_contract.py` |
| Sync phase ordering and failure status | `nexgen_core/guard.py` | `test_nexgen_phase3.py`, `test_nexgen_quality_regressions.py` |
| Skill fetch, replacement and pins | `nexgen_core/skill_sources.py` | `test_nexgen_skill_fetch.py`, `test_nexgen_skill_versions.py`, `test_nexgen_lazy_skills.py`, `test_nexgen_quality_regressions.py` |
| Host locking | `nexgen_core/lock.py` | `test_nexgen_lock.py` |
| Source checkout version fallback | `nexgen_local/version.py` | `test_nexgen_command_surface.py`, `test_nexgen_local_mcp.py` |
| Local CLI dispatch and shared config/LLM adapters | `nexgen_local/cli.py`, `nexgen_local/cmds/base.py`; domain commands in `cmds/` | `test_nexgen_command_surface.py`, `test_nexgen_local_steps.py` |
| OAuth token persistence and refresh payloads | `nexgen_local/connectors/token_store.py`; provider requests in `auth.py` and `outlook.py` | `test_nexgen_stabilization_contracts.py`, `test_nexgen_local_mcp.py` |
| Update cache, skill notices, prompts and shell/boot hooks | `nexgen_core/tools/notifier_state.py`, `notifier_skills.py`, `notifier_prompt.py`, `notifier_boot.py` | `test_nexgen_update_notifier.py`, `test_nexgen_stabilization_contracts.py` |
| Source paths, text sanitization, search terms | `nexgen_local/source_selection.py` | `test_nexgen_local.py`, `test_nexgen_local_steps.py` |
| Tool outcomes, audit receipts and resume compatibility | `nexgen_local/tools.py` | `test_nexgen_tool_outcomes.py`, `test_nexgen_local_research.py` |
| Response claims and successful read receipts | `nexgen_local/evidence.py` | `test_nexgen_local_steps.py`, `test_nexgen_local_research.py` |
| Routing and answer pipeline | `nexgen_local/engine.py` | `test_nexgen_local.py` |
| Loop state, budgets and checkpoint fields | `nexgen_local/step_state.py` | `test_nexgen_local_step_boundaries.py`, `test_nexgen_local_research.py` |
| Closed menus, argument provenance and decision prompt | `nexgen_local/step_policy.py` | `test_nexgen_local_steps.py`, `test_nexgen_local_step_boundaries.py` |
| Audited loop actions and staged proposals | `nexgen_local/step_actions.py` | `test_nexgen_local_steps.py`, `test_nexgen_local_step_boundaries.py` |
| Model decisions, bounded repairs and final loop answer | `nexgen_local/steps.py` | `test_nexgen_local_steps.py`, `test_nexgen_tool_outcomes.py` |
| Model requests and deadlines | `nexgen_local/llm.py` | `test_nexgen_llm_deadlines.py` |
| Council process lifecycle and relay checkpoints | `03-INFRA/agent-universal-layer/council/` | `test_nexgen_council_*.py` |
| Owned subprocess cleanup and Windows launch adapters | `nexgen_core/processes.py` | `test_nexgen_council_process_integration.py`, `test_nexgen_mcp_transport.py`, `test_vault_groom.py` |
| Vault publication and selected files | `nexgen_core/git_ops.py` | `test_nexgen_scoped_publish.py` |
| MCP mount policy and private connector preservation | `nexgen_core/renderer.py`; dialect writers in `mcp_render/` | `test_nexgen_mcp_preservation.py`, `test_nexgen_phase2.py`, `test_nexgen_lazy_mcp.py` |
| Lazy MCP deadlines, framing and reply correlation | `03-INFRA/agent-universal-layer/mcp/lazy-mcp.py` | `test_nexgen_mcp_transport.py`, `test_nexgen_lazy_mcp.py` |
| Released Engine update and mechanical pin | `nexgen_core/updater.py` | `test_nexgen_update_command.py` |
| Contributor lanes | `nexgen_core/lanes.py` | `test_nexgen_lanes.py` |

Check the actual filenames before selecting a test. Graph modules drive the
existing decision functions; they do not hold a second implementation.
Import helpers from their owner. `engine.py` retains compatibility exports
for older callers, but new consumers should use the owning module.

Internal retrieval uses `ToolRegistry.call_result`: `status` declares `ok`,
`empty` or `error`, `usable` determines whether to consume the result, and
`text` holds the content. Never infer an outcome from parentheses or message
wording. Tool implementations, including test doubles, record empty results
and errors explicitly. Audit must succeed before a receipt enters the registry.
Serialize receipts with `ToolCall.receipt()` so research checkpoints retain
status when resumed. Text-only methods remain available for CLI/MCP callers;
refusal-text interpretation is limited to older checkpoints without status.
Pin updates use `nexgen_core/files.py` rather than a second atomic writer.
Council artefacts and recovery snapshots use this owner too; `backup_file`
accepts an already-read text snapshot without generating a second filename policy.
MCP dialects share `McpRenderer.unmounted_server_names` for mount decisions.
Skill fetch and placement share `github_skill_source` for repository boundaries.
Skill version records must be readable maps of names to version strings.
An invalid record is preserved and reported by doctor. A failed pin write
fails the sync; changed bytes alone cannot certify a completed update.
The record's own host lock serializes its read and write, preserving pins
from concurrent writers without nesting the global sync lock.
Council, lazy MCP and Vault grooming share `nexgen_core/processes.py` for
terminating owned subprocess trees. Pass only a process group created by the
caller. Lazy MCP applies manifest startup/tool deadlines to RPC I/O, including
pipe writes, and limits received bytes to 8 MiB per exchange. Provisioning has
its own deadlines. A timed-out tool call has an unknown outcome and is never
automatically retried. Only matching JSON-RPC responses complete a request;
notifications and replies to other requests do not.
Council captures at most 8 MiB across stdout and stderr per invocation and
reads at most 8 MiB from the authoritative result file. Overflow wakes the
watchdog even when stdout is silent. `output_limit` and `invalid_output` are
terminal failures; they must not trigger automatic retries or a fallback seat.
Engine updater commands use the same process owner, with a 120-second Git
budget and a 600-second command budget. Failures after mutation begins retain
the previous commit and manual recovery instructions. Do not infer rollback
from a failed command; verify the actual ref before describing the outcome.

The plain loop and persistent research import the same state, policy and
actions. `step_policy.py` chooses admissible actions without model or tool
execution. `step_actions.py` executes admitted actions and stages proposals;
send/upload approval remains in the existing gates. `steps.py` coordinates
model decisions and answers. Its older imports remain aliases for compatibility;
new consumers import state, policy and actions directly from their owners.
Changing state fields requires checking persisted research compatibility.

`cmds/base.py` owns the local CLI configuration and model adapter, including
the compatibility boundary for older callers that patch `cli._config` or
`cli._llm`. Domain commands import those helpers directly. `update_notifier.py`
keeps the command entry points; cache, skill notices, prompts and boot hooks
each have their own owner. Shell hook templates live only in `notifier_boot.py`.
Google and Outlook share token publication and refresh-payload merging through
`token_store.py`, while each provider retains its endpoint and scopes.
Failure diagnostics around credentials and manifests log the operation and
exception type; raw exception messages can include token URLs or configuration
contents. Keep those payloads out of logs.

For a bug, first add a test that fails for the reported behavior. Check the
failure path as well as success: a failed write must preserve the old bytes,
a failed replacement must restore the active source, and an operational
model error must not cause an unrequested retry. Re-run the relevant tests
after the fix, inspect the diff, then run the complete gate before merging.
Record any untested platform or live provider; a synthetic endpoint proves
the driver's behavior, not a provider's production availability.

### Working beside an installed engine

Follow the durable lane contract in [AGENTS.md](AGENTS.md). If another session
uses the same checkout, create a separate Git worktree. Switching branches
in a shared directory does not isolate the two sessions' files.

Prefer the regression suite's synthetic fixtures for runtime changes. Tests
redirect the affected paths and disable host mutations. The shared fixture
removes inherited config/state overrides; override tests supply temporary
locations explicitly. A manual sync is a
live write operation unless you deliberately isolate it: `NEXGEN_HOME`
changes the default home, but `CODEX_HOME`, `XDG_CONFIG_HOME`,
`AGENT_STATE_DIR` and `AGENT_VAULT_DATA` can still select explicit locations.
Use disposable locations for all of them and set
`NEXGEN_DISABLE_HOST_MUTATIONS=1` before a manual experiment. Never point a
development sync at an installed configuration to see what happens.

### Before tagging a release

```sh
python3 03-INFRA/scripts/nexgen_core/release.py preflight
```

It checks the things that have each been forgotten at least once: that
`VERSION` is a real version and newer than the newest tag, that the launchers
the previous release's symlinks point at match the table that generates them,
that no private maintainer tooling reached the public tree, and that the lint
gate passes rather than having been regenerated.

## What CI checks on every PR

All of these run automatically (`.github/workflows/ci.yml`) and must pass:

- Python/YAML/shell syntax checks, plus `install.sh --check`
- A leak-scan over every commit newly introduced by the PR (secrets,
  hardcoded personal paths — see `SECURITY.md`)
- `ruff` against a committed baseline (`03-INFRA/ruff-baseline.json`) —
  fails only on a *new* finding or an existing one getting worse, not on
  pre-existing debt
- `shellcheck` on every `.sh` file
- `pip-audit` on every `requirements*.txt`
- `docker compose config` validation for every deploy stack
- `PSScriptAnalyzer` static analysis on every `.ps1` file
- A live smoke test of the bundled `vault-mcp` server (build, run, real
  MCP write path, commit verification)
- The full pytest regression suite, on both `ubuntu-latest` and
  `windows-latest`

A PR with red CI won't be merged. If a check fails and you believe it's
wrong (a false positive in the leak-scan, for instance), say so in the PR —
don't work around the gate.

## Scope guardrails worth knowing before you start

- **Notes vs. infra have separate write paths.** If your change touches
  how the vault is written to, preserve the documented write boundary in
  `README.md` and test the real MCP commit path.
- **Cross-platform is part of "done."** A runtime change needs the Linux and Windows test gates, or an explicit
  documented limit. Put shared behavior in Python; keep shell launchers thin. See the "Definition of done
  cross-platform" rule in
  `03-INFRA/agent-universal-layer/instructions/AGENTS.md`.
- **Don't hand-edit generated files.** `render.py`, `agent_sync.py`, and
  `skills-sync.py` generate per-CLI configuration from canonical manifests.
  If a generated dialect is wrong, fix the generator, not the output.

## Publishing

Target `developer` for integration, following `AGENTS.md`. `main` is frozen
history. Release branches advance from `developer` only at release time;
passing tests does not authorize a release. The maintainer integrates only
after the required CI checks and signing requirements are satisfied.

## License

By submitting a pull request, you agree that your contribution may be
included in this project under its existing license
([PolyForm Noncommercial 1.0.0](LICENSE)).
