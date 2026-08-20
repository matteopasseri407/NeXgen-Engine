"""Codex adapter: 'bypass' posture only in ~/.codex/config.toml, no guardrail.

Only 'bypass' has a verified rendering (against the installed binary's
one-shot --dangerously-bypass-approvals-and-sandbox flag): accept-edits/ask
have no confirmed default and are deliberately left out.

No guardrail for Codex, neither here nor ever without an explicit decision
from whoever owns the machine: its hooks sit behind a per-hash consent that
only a person can give in an interactive session (or a
--dangerously-bypass-hook-trust flag that no provisioner should press on its
own). A hook written here wouldn't run until that decision is made
elsewhere -- a silent gap that looks like a working guardrail, worse than
having none at all. agent-doctor flags this on every run: Codex is the only
CLI that runs without network access, by design.
"""
from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

from nexgen_core.runtimes.base import GuardrailError, Runtime

_POSTURE_RENDER = {"bypass": {"approval_policy": "never", "sandbox_mode": "danger-full-access"}}


def _root_table_end(lines: list[str]) -> int:
    """Index of the first `[table]` line: everything before it (blank
    lines, comments, bare keys) is root table territory."""
    for i, line in enumerate(lines):
        if re.match(r"^[ \t]*\[", line):
            return i
    return len(lines)


def _set_root_string(text: str, key: str, value: str) -> str:
    """Surgically sets ONE root-table string key: touches only that line
    (or inserts one before the first [table]), leaving every comment and
    section intact -- never a full rewrite of the user's config.toml. The
    result is re-parsed and verified before being returned."""
    lines = text.split("\n")
    root_end = _root_table_end(lines)
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$")
    rendered = f'{key} = "{value}"'
    for i in range(root_end):
        if pattern.match(lines[i]):
            lines[i] = rendered
            break
    else:
        lines.insert(root_end, rendered)
    result = "\n".join(lines)
    reparsed = tomllib.loads(result)
    if reparsed.get(key) != value:
        raise GuardrailError(f"codex: could not set the TOML key {key!r}")
    return result


class CodexRuntime(Runtime):
    name = "codex"

    def _config_path(self, home: Path) -> Path:
        return home / ".codex" / "config.toml"

    def is_installed(self, home: Path) -> bool:
        del home
        # config.toml is NOT a valid signal: this same layer's MCP renderer
        # creates it from scratch on every cycle even on a machine where
        # Codex was never installed. Only the binary on the PATH is a
        # footprint that belongs exclusively to the product.
        return bool(shutil.which("codex"))

    def read_posture(self, home: Path) -> str | None:
        path = self._config_path(home)
        if not path.is_file():
            return None
        try:
            current = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            return None
        for posture, desired in _POSTURE_RENDER.items():
            if all(current.get(k) == v for k, v in desired.items()):
                return posture
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        desired = _POSTURE_RENDER.get(posture)
        if desired is None:
            return None
        path = self._config_path(home)
        if not path.is_file():
            return None  # Codex never launched here: no posture to apply
        raw = path.read_text(encoding="utf-8")
        try:
            current = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            raise GuardrailError(f"codex: {path} is not valid TOML ({exc})") from exc
        if all(current.get(k) == v for k, v in desired.items()):
            return None
        text = raw
        for key, value in desired.items():
            text = _set_root_string(text, key, value)
        self.backup(path)
        self.atomic_write(path, text)
        return f"codex: posture '{posture}' applied in {path}"

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        del home, hook_source, engine_hooks_dir
        return None  # No verified guardrail hookup for Codex (see above)
