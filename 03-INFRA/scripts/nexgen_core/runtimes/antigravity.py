"""Antigravity adapter: the PreToolUse hook in ~/.gemini/config/hooks.json.

Antigravity has a REAL command-line hook (unlike OpenCode's in-process
callback), but with its own JSON shape: the static adapter at
agent-universal-layer/hooks/antigravity-guardrail-adapter.mjs translates
{toolCall, workspacePaths, ...} into the same stdin/stdout the guardrail
body already speaks for Claude.

`toolPermission` and `artifactReviewPolicy` are Antigravity's own settings, written
by the app when a person picks how much it may do unattended: this adapter never
writes them.
"""
from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.paths import (
    ANTIGRAVITY_APP_DATA_DIRS,
    ANTIGRAVITY_APP_FOOTPRINTS,
    ANTIGRAVITY_CLI_FOOTPRINTS,
    antigravity_cli_state,
    antigravity_hooks,
    antigravity_settings,
)
from nexgen_core.runtimes.base import GUARDRAIL_SIDECAR_NAME, GuardrailError, Runtime

_IS_WINDOWS = platform.system() == "Windows"

_ADAPTER_NAME = "antigravity-guardrail-adapter.mjs"


def _app_ran_here(home: Path) -> bool:
    """Whether the Antigravity App or the IDE has left its own state in this home.

    The App and the IDE read the same global `~/.gemini/config/hooks.json` the `agy`
    CLI does, so on a machine with the app but no CLI this is the only evidence that
    the guardrail has a reader. The data folder itself is not enough: the MCP renderer
    fans its own `mcp_config.json` into it, so what counts is a file only the product
    writes there, namely the install id it records on first launch and the `brain`
    folder the hooks page names as the transcript root.
    """
    gemini = home / ".gemini"
    return any(
        (gemini / directory / footprint).exists()
        for directory in ANTIGRAVITY_APP_DATA_DIRS
        for footprint in ANTIGRAVITY_APP_FOOTPRINTS
    )


class AntigravityRuntime(Runtime):
    name = "antigravity"

    def _settings_path(self, home: Path) -> Path:
        return antigravity_settings(home)

    def _hooks_path(self, home: Path) -> Path:
        return antigravity_hooks(home)

    def is_installed(self, home: Path) -> bool:
        if shutil.which("agy"):
            return True
        names = ("agy.exe", "agy.cmd", "agy") if _IS_WINDOWS else ("agy",)
        if any((home / ".local" / "bin" / name).is_file() for name in names):
            return True
        if _app_ran_here(home):
            return True
        # settings.json is written only by Antigravity itself on first launch, and so are
        # the CLI's history, its conversation store and its log: any of them is the
        # product's own footprint. `skills/` is not on that list on purpose, because this
        # very layer's skill materializer creates it on a machine with no Antigravity at all
        # -- the defect `test_antigravity_is_installed_regression_directory_footprint` pins.
        state = antigravity_cli_state(home)
        return any((state / name).exists() for name in ANTIGRAVITY_CLI_FOOTPRINTS)

    def _load_json(self, path: Path, *, label: str) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GuardrailError(f"antigravity: {label} is not valid JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise GuardrailError(f"antigravity: the root of {label} is not an object")
        return data

    def guardrail_sidecar(self, home: Path) -> Path | None:
        return self._hooks_path(home).parent / GUARDRAIL_SIDECAR_NAME

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        if not self._settings_path(home).is_file() and not _app_ran_here(home):
            # Neither `agy` nor the App/IDE ever launched here: the hook would have no reader.
            return None
        hooks_path = self._hooks_path(home)
        adapter_dir = hooks_path.parent

        body_dst = adapter_dir / "nexgen-guardrail-hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())

        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"antigravity: missing engine adapter ({adapter_src})")
        adapter_dst = adapter_dir / _ADAPTER_NAME
        adapter_changed = self.deploy_bytes(adapter_dst, adapter_src.read_bytes())
        adapter_changed |= self.deploy_guardrail_core(adapter_dir, engine_hooks_dir)

        sidecar_path = adapter_dir / GUARDRAIL_SIDECAR_NAME
        # Antigravity's external hook timeout kills the WHOLE command; the
        # adapter already applies its own per-body timeout via subprocess,
        # so the buffer here keeps the external kill from firing before the
        # internal one gets its chance.
        timeout = 5
        # `strict`: a guardrail that cannot answer must deny, never ask. Nothing here observes a
        # posture any more, and `always-proceed` skips an ask, so ask is no brake at all.
        sidecar_changed = self.write_guardrail_sidecar(
            sidecar_path, body=body_dst, home=home, timeout=timeout, strict=True,
        )

        # Antigravity executes hooks with the hooks.json directory (adapter_dir) as CWD.
        # Running via relative path avoids quotes (which agy on Windows passes literally
        # to node, causing module resolution failure) and avoids issues when home has spaces.
        adapter_rel = adapter_dst.relative_to(adapter_dir).as_posix()
        desired_entry = {
            "enabled": True,
            "PreToolUse": [{
                "matcher": "run_command",
                "hooks": [{"type": "command", "command": f"node {adapter_rel}", "timeout": timeout + 5}],
            }],
        }
        current = self._load_json(hooks_path, label="hooks.json") or {}
        # Only its own key: any other hook -- the user's or another tool's
        # -- in this file stays exactly as it was.
        if current.get("nexgen-guardrail") == desired_entry:
            registered = False
        else:
            self.backup(hooks_path)
            updated = {**current, "nexgen-guardrail": desired_entry}
            self.atomic_write(hooks_path, json.dumps(updated, indent=2) + "\n")
            registered = True

        if registered:
            return f"antigravity: guardrail registered in {hooks_path}"
        if body_changed or adapter_changed or sidecar_changed:
            return f"antigravity: guardrail body/adapter updated in {adapter_dir}"
        return None
