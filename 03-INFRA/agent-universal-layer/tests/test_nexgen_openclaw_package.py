"""The `nexgen` plugin as a package: one file list, one reproducible tarball, no private bytes.

Every test builds into a temporary home and a temporary hooks folder, and none of them runs
`openclaw`: the owner's `~/.openclaw`, the real state folder and a real gateway are never
touched.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import REAL_UL
from nexgen_core.paths import openclaw_config
from nexgen_core.runtimes.base import GUARDRAIL_SIDECAR_NAME
from nexgen_core.runtimes.openclaw import (
    PLUGIN_CONFIG_NAME,
    PLUGIN_ID,
    OpenClawRuntime,
    plugin_files,
)

HOOKS = REAL_UL / "hooks"
PLUGIN_SOURCE = HOOKS / "openclaw-guardrail-plugin"


def a_home(tmp_path: Path) -> Path:
    """A home whose OpenClaw already has a state folder: that is where the deposit goes."""
    home = tmp_path / "home"
    config = openclaw_config(home)
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"gateway": {"port": 18789}}), encoding="utf-8")
    return home


def a_policy_body(tmp_path: Path) -> Path:
    body = tmp_path / "guardrail-body.mjs"
    body.write_text("// the owner's private policy, in the test only\n", encoding="utf-8")
    return body


def test_the_deposit_writes_exactly_the_files_on_the_plugins_one_list(tmp_path):
    home = a_home(tmp_path)

    OpenClawRuntime().install_guardrail(home, a_policy_body(tmp_path), HOOKS)

    deposited = {
        path.name: path
        for path in (home / ".openclaw" / "extensions" / PLUGIN_ID).iterdir()
        if path.is_file()
    }
    engine_owned = {PLUGIN_CONFIG_NAME, GUARDRAIL_SIDECAR_NAME}
    listed = plugin_files(HOOKS)

    assert set(deposited) - engine_owned == set(listed)
    for name, source in listed.items():
        assert deposited[name].read_bytes() == source.read_bytes(), name
