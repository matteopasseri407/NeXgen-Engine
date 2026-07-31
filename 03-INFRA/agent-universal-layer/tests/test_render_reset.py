"""Onboarding reset (v0.92 slice 5): render.py --reset backs up + removes a
CLI's config; render.py --revert restores it. Reset and revert are a pair.

G2-reset (2026-07-30 incident): --reset used to unconditionally unlink ANY
CLI's config, including ~/.claude.json, which also carries account/trust-list
state that no script can regenerate. A user following INIT.md's onboarding
reset ended up logged out of Claude, with the tool's own printed advice
("agent-sync apply") powerless to fix it. --reset is now refused for a CLI
whose writer cannot recreate an absent config from scratch; it stays allowed
for antigravity/opencode, whose writers learned to bootstrap a missing file
as part of BUG-A."""
from __future__ import annotations

from conftest import load_render_module

import pytest


def test_reset_is_noop_when_config_absent(sandbox):
    mod = load_render_module(sandbox)
    assert mod.cmd_reset("claude") == 0                       # nothing to reset


@pytest.mark.parametrize("cli", ["antigravity", "opencode"])
def test_reset_backs_up_and_removes_a_recreatable_config(sandbox_with_live_configs, cli):
    mod = load_render_module(sandbox_with_live_configs)
    path = mod._cli_config_path(cli)
    original = path.read_text("utf-8")
    assert path.exists()

    rc = mod.cmd_reset(cli)

    assert rc == 0
    assert not path.exists()                                  # removed
    baks = sorted(path.parent.glob(path.name + ".bak-*"))
    assert baks and baks[-1].read_text("utf-8") == original   # backed up first


@pytest.mark.parametrize("cli", ["antigravity", "opencode"])
def test_revert_restores_a_reset_config(sandbox_with_live_configs, cli):
    """The undo --reset prints must actually undo it, for both CLIs it allows.

    opencode is the interesting half: its path is resolved dynamically from
    whichever candidate filename currently exists (opencode.jsonc / .json /
    config.json), so once --reset removes the live file the resolution falls
    back to the .jsonc default and stops matching the backup's name. --revert
    then reported "nothing to revert" while the user's config sat right next
    to it as an orphan .bak-*. Fixed on 2026-07-31 by searching every
    candidate filename when the resolved path is gone."""
    mod = load_render_module(sandbox_with_live_configs)
    path = mod._cli_config_path(cli)
    original = path.read_text("utf-8")

    mod.cmd_reset(cli)
    assert not path.exists()

    rc = mod.cmd_revert(cli)

    assert rc == 0
    assert path.exists()
    assert path.read_text("utf-8") == original                # fully restored


@pytest.mark.parametrize("cli", ["antigravity", "opencode"])
def test_reset_then_write_recreates_a_clean_config(sandbox_with_live_configs, monkeypatch, cli):
    """The actual guarantee behind allowing --reset for these two: after
    BUG-A, the CLI's own --write path can recreate the file from scratch, so
    the onboarding cycle (reset -> re-provision) genuinely works instead of
    stranding the user with a missing config forever. Re-resolves the path
    AFTER writing (not before): OpenCode's default filename on a truly fresh
    provision is opencode.jsonc regardless of what the old, now-deleted file
    was named.

    The presence probe is faked deliberately. Once --reset removes the config,
    recreating it depends on the CLI reading as installed, and both probes
    look at the real machine (the binary on PATH). Left alone, this test
    passed on a developer box that happens to have `agy` and `opencode`, and
    failed in CI, which has neither -- the host deciding the verdict instead
    of the code."""
    mod = load_render_module(sandbox_with_live_configs)
    binary = {"antigravity": "agy", "opencode": "opencode"}[cli]
    monkeypatch.setattr(mod.shutil, "which", lambda cmd: f"/usr/bin/{binary}" if cmd == binary else None)
    path_before = mod._cli_config_path(cli)

    assert mod.cmd_reset(cli) == 0
    assert not path_before.exists()

    write_fn = {"antigravity": mod.write_antigravity, "opencode": mod.write_opencode}[cli]
    rc = write_fn()

    assert rc == 0
    path_after = mod._cli_config_path(cli)
    assert path_after.exists()
    key = {"antigravity": "mcpServers", "opencode": "mcp"}[cli]
    data = mod.parse_jsonc(path_after.read_text("utf-8"))
    assert "fake-stdio-tool" in data[key]


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_reset_refuses_a_cli_without_a_recreation_path(sandbox_with_live_configs, cli, capsys):
    mod = load_render_module(sandbox_with_live_configs)
    path = mod._cli_config_path(cli)
    original = path.read_text("utf-8")
    existing_baks = sorted(path.parent.glob(path.name + ".bak-*"))

    rc = mod.cmd_reset(cli)
    out = capsys.readouterr().out

    assert rc == 2
    assert "REFUSED" in out
    assert path.exists()
    assert path.read_text("utf-8") == original, f"{cli}: the config must stay untouched on refusal"
    assert sorted(path.parent.glob(path.name + ".bak-*")) == existing_baks, (
        f"{cli}: refusing the reset must not create a new backup either"
    )


def test_reset_refusal_message_flags_claudes_account_state(sandbox_with_live_configs, capsys):
    mod = load_render_module(sandbox_with_live_configs)
    mod.cmd_reset("claude")
    out = capsys.readouterr().out
    assert "account" in out.lower()
