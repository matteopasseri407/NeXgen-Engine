# Changelog

All notable changes to the NeXgen engine are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[Semantic Versioning](https://semver.org/).

This file tracks the **engine** (this repo). Your own data — manifests,
instructions, skills, secrets — lives in your KnowledgeVault and is not part
of any engine release.

## [Unreleased]

### Added

- `agent-skill list|find|show|path`, the small cross-platform command for
  loading exactly one managed skill body on demand.
- Explicit `exposure: manual|core` in the skill manifest, plus a generated
  safe catalog and a one-time `--migrate-legacy` quarantine for old eager
  folders.
- A data-owned `sync/remotes.yaml` policy, typed pull states, and a host-wide
  lock for the complete sync transaction.

### Changed

- Managed skill bodies now live in `~/.agents/skill-library/`, outside eager
  discovery roots. Only explicitly core bodies enter `~/.agents/skills/` or
  Codex's runtime view. Claude retains declared native-lazy views.
- `agent-sync` normalizes unsafe whole-root links before materializing skill
  views, and `agent-doctor` verifies the library, catalog, and core exposure.
- `guard` and `apply` now regenerate runtime derivatives only after proving
  the vault fresh against its authoritative remote. Required phase failures
  are aggregated into a non-zero exit code. Publishing is a separate action,
  with configured mirrors downstream of the authoritative remote.
- Running `agent-sync` without arguments is help-only. The implicit combined
  `full` operation was removed.

### Fixed

- Legacy migration preserves declared Claude native-lazy links instead of
  treating them as stale eager copies.
- Dirty, wrong-branch, ahead, diverged, missing-remote, fetch-failed, and
  malformed-manifest states can no longer degrade into a successful-looking
  propagation run.
- The distributed MCP manifest's `filesystem` server no longer mounts the
  user's entire home (a bare `${HOME}` argument). It now mounts two
  explicit, configurable roots — `AGENT_ENGINE_ROOT` and `AGENT_VAULT_DATA`,
  the same canonical engine/data roots the rest of the layer already
  resolves — and a user can add more roots as extra `args` entries. The
  `memory` server is no longer mounted by default: it required
  `MCP_MEMORY_OPT_IN` because it is a second, non-authoritative memory
  channel outside the KnowledgeVault.

## [0.3.2] - 2026-07-10

### Fixed

- Windows CI no longer applies POSIX mode-bit assertions to NTFS files.
  The test still verifies that the generated configuration and backup exist;
  owner-only mode checks remain enforced on POSIX, where they are meaningful.

## [0.3.1] - 2026-07-10

### Fixed

- Windows runtime skill directories backed by Junctions now recover safely.
  The provisioner recognizes directory reparse points even on Python builds
  without `Path.is_junction()`, removes a whole-hub loop through the shared
  path adapter, and preserves per-skill Junctions already pointing at their
  hub source instead of recursing into them.

## [0.3.0] - 2026-07-09

### Added

- `AI-INSTALLER.md` / `AI-UNINSTALL.md`: fast, autonomous companions to
  `INIT.md` / `docs/uninstall.md` for an agent to run with minimal
  back-and-forth. Both defer to the existing guide for the actual
  mechanism (no duplicated/divergent instructions) and require explicit
  confirmation before any destructive step.
- `agent-doctor`: a short, pruneable "third-party CLI compatibility" check
  that flags a known-broken Codex CLI release (a real tool-dispatcher
  regression, not a general version pin) instead of failing silently or
  mysteriously when every tool call gets rejected.

## [0.2.0] - 2026-07-09

### Added

- Anti-leak gate (`engine-push`, pre-commit/commit-msg hooks, CI leak-scan)
  guarding every push to this repo: a single blocked finding stops the push.
- Regression test suite (`tests/run.sh`, 40 pytest cases) covering render.py,
  the provisioner, skills-sync.py and agent-doctor.sh in a sandboxed HOME.
- `agent_sync.py`: single cross-platform provisioner replacing the old
  `agent-sync.sh` / `agent-sync.ps1` duplication. The `.sh`/`.ps1` files are
  now 5-line launchers; same CLI, same exit codes, same log file.
- CI job `engine-tests-windows` (pytest on `windows-latest`), so Windows
  coverage no longer depends on physical access to a Windows machine.
- Consumer engine clone version-pin check in `agent-doctor` (S2): flags
  silent drift between the pinned commit and what is actually checked out.
- Data-schema migration framework (`data_migrations()` in `agent_sync.py`):
  versioned, idempotent, backs up affected files before writing. No
  migrations are registered yet — today's data shape is the baseline.
- `VERSION` file and this changelog.
- Path-traversal guard in `skills-sync.py`'s GitHub-origin skill installer.
- Atomic writes (temp file + replace) for live config files the provisioner
  regenerates on every run (`settings.json`, `CLAUDE.md`, the systemd unit,
  generated MCP configs).

### Changed

- All engine strings are English-only. Localizing alerts is a user-data
  concern: the engine calls an optional translator script if the vault
  provides one, falling back silently to English otherwise.
- The systemd timer persists `AGENT_ENGINE_ROOT`/`AGENT_VAULT_DATA` across a
  cutover instead of reverting to the default layout on the next run.
- Personal instance data (the user's own `AGENTS.md`, MCP manifest) is
  always resolved from the data root, never from wherever the engine happens
  to be installed.

### Fixed

- Several engine/data path-resolution bugs where a script silently fell back
  to reading the personal data copy instead of the installed engine after a
  cutover (`agent-doctor`, `skills-sync.py`, the provisioner itself).
- Fresh install with no skills manifest yet: `skills-sync.py` no longer
  crashes, and `agent-doctor`'s skill check no longer hardcodes anyone's
  personal skill names — zero configured skills is a warning, not a
  permanent failure.
- OCR MCP server: read-before-size-check memory exhaustion, double file
  read, and unsanitized multipart filename header injection.
- Symlink race (CWE-59) in a script's temp-file handling.
- A lifecycle-audit script silently auditing the wrong directory when run
  from the engine clone instead of the data root.
- Restored an executable bit lost since the first public release.

### Removed

- `agent-healthcheck.sh`: dead code, fully superseded by `agent_sync.py`'s
  built-in healthcheck step.

## [0.1.0] - 2026-07-07

Initial public release: repositioned as an AgentOps control layer, hardened
the public trust surface, calibrated the README's claims against what the
engine actually does today.
