"""Pinning dello split runtimes/claude prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con config finte:
install_guardrail su config finta stabile, firme pubbliche invariate.
Solo config finte in tmp_path, mai config vere, mai rete.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.runtimes.claude import ClaudeRuntime  # noqa: E402


def _engine_hooks(tmp_path: Path) -> Path:
    engine_hooks = tmp_path / "engine-hooks"
    engine_hooks.mkdir(parents=True, exist_ok=True)
    (engine_hooks / "claude-guardrail-adapter.mjs").write_text("// adapter\n", encoding="utf-8")
    (engine_hooks / "nexgen-guardrail-core.mjs").write_text("// core\n", encoding="utf-8")
    return engine_hooks


def test_install_guardrail_su_config_finta_stabile(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    settings = claude_dir / "settings.json"
    settings.write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "node other.mjs"}]}]}}
        ),
        encoding="utf-8",
    )
    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy body\n", encoding="utf-8")

    rt = ClaudeRuntime()
    action = rt.install_guardrail(home, hook_source, _engine_hooks(tmp_path))
    assert action is not None and "claude" in action

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["hooks"]["SessionStart"] == [
        {"hooks": [{"type": "command", "command": "node other.mjs"}]}
    ]
    pretool = data["hooks"]["PreToolUse"]
    assert pretool[0]["matcher"] == "Bash"
    assert pretool[0]["hooks"][0]["command"] == f'node "{claude_dir / "claude-guardrail-adapter.mjs"}"'
    assert pretool[0]["hooks"][0]["timeout"] == 10

    body_dst = claude_dir / "nexgen-guardrail-hooks" / "guardrail-catastrophic.mjs"
    assert body_dst.read_text(encoding="utf-8") == "// policy body\n"
    assert (claude_dir / "claude-guardrail-adapter.mjs").is_file()
    assert (claude_dir / "nexgen-guardrail-core.mjs").is_file()

    after_first = settings.read_text(encoding="utf-8")
    again = rt.install_guardrail(home, hook_source, _engine_hooks(tmp_path))
    assert again is None
    assert settings.read_text(encoding="utf-8") == after_first
    assert len(json.loads(after_first)["hooks"]["PreToolUse"]) == 1


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(ClaudeRuntime.is_installed).parameters) == ["self", "home"]
    assert list(inspect.signature(ClaudeRuntime.read_posture).parameters) == ["self", "home"]
    assert list(inspect.signature(ClaudeRuntime.apply_posture).parameters) == [
        "self",
        "home",
        "posture",
    ]
    assert list(inspect.signature(ClaudeRuntime.guardrail_sidecar).parameters) == ["self", "home"]
    assert list(inspect.signature(ClaudeRuntime.install_guardrail).parameters) == [
        "self",
        "home",
        "hook_source",
        "engine_hooks_dir",
    ]
    assert list(inspect.signature(ClaudeRuntime.remove_event_sink).parameters) == ["self", "home"]
    public = {
        name
        for name, value in vars(ClaudeRuntime).items()
        if callable(value) and not name.startswith("_")
    }
    assert public == {
        "is_installed",
        "read_posture",
        "apply_posture",
        "guardrail_sidecar",
        "install_guardrail",
        "remove_event_sink",
    }
