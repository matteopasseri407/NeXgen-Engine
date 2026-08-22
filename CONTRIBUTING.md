# Contributing

This is a solo-maintainer project. Contributions are welcome,
but set expectations accordingly: review can take a while, and not every PR
fits the project's scope (see "Design boundaries" and "Scope and
limitations" in `README.md`) even when it's well built.

## Before you write code

For anything bigger than a small fix, open an issue first describing the
problem and your proposed approach. This saves you from building something
that doesn't fit the project's direction. For a small, obvious fix (typo,
broken link, clear bug with an obvious one-line fix), a PR alone is fine.

## Dev setup

- Python 3.11+ with `pyyaml` (`pip install pyyaml`), or 3.10 with `tomli`
  too.
- The regression suite lives at
  `03-INFRA/agent-universal-layer/tests/`; run it with `python3 -m pytest`
  (from the repo root or from that directory — `pyproject.toml` points
  `testpaths` at it either way). It's sandboxed and never touches your real
  `$HOME`.
- `bash install.sh --check` runs the same preflight the installer runs —
  useful to confirm your environment has what the project expects. In
  `--check` mode it writes nothing at all.

### Working on the engine while running it

If you already use this engine on the machine you develop on, run the checkout
under its own home:

```sh
export NEXGEN_HOME="$HOME/nexgen-dev"
python3 03-INFRA/scripts/nexgen_core/cli/__init__.py sync
```

`NEXGEN_HOME` moves everything the engine writes — the commands it installs,
the runtime configurations it generates, the skill library, the scheduled
units, its own state — into that directory. Your real setup keeps its
commands, its connectors and its memories, and the two never meet. Point
`AGENT_VAULT_DATA` at a throwaway vault too if you would rather not read your
real one.

Without `NEXGEN_HOME` the engine works against your actual home, which is what
an installed engine is supposed to do. There is no dry-run flag standing in
for this: a run that writes somewhere else is easier to trust than a run that
promises not to write.

A test enforces it. `test_nexgen_takeover.py` fails if any module reads the
home directory itself instead of asking for it, because one forgotten call is
all it takes for a development checkout to overwrite a working installation.

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
  how the vault is written to, read `03-INFRA/vault-write-architecture.md`
  first — "one door per kind of thing" is a deliberate design constraint,
  not an accident to route around.
- **Cross-platform is part of "done."** A change to `agent_sync.py` or the
  shell scripts needs both OS dialects (`.sh`/`.ps1`) covered, or an
  explicit, documented reason why not. See the "Definition of done
  cross-platform" rule in
  `03-INFRA/agent-universal-layer/instructions/AGENTS.md`.
- **Don't hand-edit generated files.** `render.py`, `agent_sync.py`, and
  `skills-sync.py` generate per-CLI configuration from canonical manifests.
  If a generated dialect is wrong, fix the generator, not the output.

## Publishing

Only the maintainer publishes to this repository's `main` branch. As a
contributor, open a normal pull request from your fork; the maintainer merges
only after the required CI checks and signing requirements are satisfied.

## License

By submitting a pull request, you agree that your contribution may be
included in this project under its existing license
([PolyForm Noncommercial 1.0.0](LICENSE)).
