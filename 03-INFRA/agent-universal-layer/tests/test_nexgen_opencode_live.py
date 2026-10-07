"""The guardrail plugin against the real OpenCode binary, when there is one on this machine.

Everything else about the OpenCode guardrail is tested against a model of OpenCode written from its docs and its
config, and that model was wrong twice: a plugin registered as a FILE (what this engine did) is refused by OpenCode
2.0.24 ("configured plugin path must be a directory"), and the hooks it used (`permission.ask`) are never called.
The guardrail sat on disk, registered, never loaded, in the posture where it was the only brake, and nothing said so.
This starts the real binary in an isolated home (a private server, so a running OpenCode is never touched) and reads
its own log: the plugin directory must be loaded, by itself, with nothing registered.

No model is needed: the plugins load before the model is looked up, so a model that does not exist is enough and the
run takes about a second. Skipped where `opencode` is not installed (the CI has none).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.runtimes.opencode import OpenCodeRuntime  # noqa: E402

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
OPENCODE = shutil.which("opencode")

pytestmark = pytest.mark.skipif(OPENCODE is None or os.name == "nt", reason="the opencode binary is not installed here")


def test_the_real_opencode_loads_the_guardrail_plugin_directory_by_itself(tmp_path):
    home = tmp_path / "home"
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.jsonc").write_text('{"$schema": "https://opencode.ai/config.json"}\n', encoding="utf-8")
    body = tmp_path / "body.mjs"
    body.write_text("process.stdin.resume(); process.stdin.on('end', () => process.exit(0));\n", encoding="utf-8")

    rt = OpenCodeRuntime()
    assert rt.install_guardrail(home, body, HOOKS) is not None
    rt.apply_posture(home, "bypass")
    config = json.loads((config_dir / "opencode.jsonc").read_text(encoding="utf-8"))
    assert not config.get("plugins") and not config.get("plugin"), "nothing is registered: OpenCode loads the directory itself"

    work = tmp_path / "work"
    work.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "NEXGEN_HOME": str(home),
        "AGENT_STATE_DIR": str(home / "state"),
    }
    # `--standalone`: a private server. Without it the command would talk to the person's own running OpenCode.
    subprocess.run([OPENCODE, "run", "--standalone", "--model", "nexgen-test/no-such-model", "hi"],
                   cwd=work, env=env, capture_output=True, text=True, timeout=120, check=False)

    logs = list((home / ".local" / "share" / "opencode" / "log").glob("*.log"))
    assert logs, "OpenCode did not write its log where the test expects it"
    text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in logs)
    plugin_lines = [line for line in text.splitlines() if "nexgen-guardrail" in line]
    assert any("loading plugin" in line for line in plugin_lines), "the guardrail plugin was not loaded: " + "\n".join(plugin_lines)[:600]
    assert not any("failed to load" in line or "must be a directory" in line for line in plugin_lines), "\n".join(plugin_lines)[:600]
