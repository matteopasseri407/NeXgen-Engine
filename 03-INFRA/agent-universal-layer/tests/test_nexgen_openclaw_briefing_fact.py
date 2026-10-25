"""The engine's own continuity is switched on by a fact that lives on this machine.

The briefing text and the checkpoint are the engine's, deposited beside the plugin.
Their switches used to follow whether Claude Code had registered its checkpoint
hook, so on a machine where OpenClaw is the only assistant they were off while the
text sat right there. And OpenClaw's built-in memory plugin is no longer part of the
base set: the Vault is the memory, and a second one running beside it is the
divergence this engine exists to prevent.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.profile import BASE_PLUGINS  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.runtimes.openclaw import BRIEFING_NAME, OpenClawRuntime  # noqa: E402 - sys.path shim for cloned checkout


def _plugin_dir(home: Path) -> Path:
    runtime = OpenClawRuntime()
    folder = runtime._plugin_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_the_briefing_follows_its_own_text_not_another_runtime(tmp_path):
    runtime = OpenClawRuntime()
    folder = _plugin_dir(tmp_path)

    assert runtime._briefing_installed(tmp_path) is False

    (folder / BRIEFING_NAME).write_text("briefing\n", encoding="utf-8")

    assert runtime._briefing_installed(tmp_path) is True
    assert runtime._checkpoint_installed(tmp_path) is True


def test_the_native_memory_plugin_is_not_part_of_the_base_set():
    assert "memory-core" not in BASE_PLUGINS
