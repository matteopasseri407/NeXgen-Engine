"""Codex adapter: no guardrail hookup and no permission writing.

Codex has no verified guardrail hookup, neither here nor ever without an explicit
decision from whoever owns the machine: its hooks sit behind a per-hash consent that
only a person can give in an interactive session (or a
`--dangerously-bypass-hook-trust` flag that no provisioner should press on its
own). A hook written here wouldn't run until that decision is made
elsewhere -- a silent gap that looks like a working guardrail, worse than
having none at all. agent-doctor flags this on every run: Codex is the only
CLI that runs without network access, by design.

`approval_policy` and `sandbox_mode` are Codex's own settings, written by Codex when
a person picks Approvals or a sandbox: this adapter never writes them.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from nexgen_core.runtimes.base import Runtime


class CodexRuntime(Runtime):
    name = "codex"

    def is_installed(self, home: Path) -> bool:
        del home
        # config.toml is NOT a valid signal: this same layer's MCP renderer
        # creates it from scratch on every cycle even on a machine where
        # Codex was never installed. Only the binary on the PATH is a
        # footprint that belongs exclusively to the product.
        return bool(shutil.which("codex"))

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        del home, hook_source, engine_hooks_dir
        return None  # No verified guardrail hookup for Codex (see above)

