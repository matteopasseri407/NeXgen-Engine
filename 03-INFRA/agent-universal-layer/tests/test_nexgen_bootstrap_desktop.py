"""Install-time absorb offer: the Claude Desktop app's MCP servers surface in `nexgen init`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import renderer_cli  # noqa: E402
from nexgen_core.bootstrap import desktop_app_orphans, print_desktop_stubs  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch):
    # Path resolution honors XDG_CONFIG_HOME (POSIX) and APPDATA (Windows):
    # without this, a host carrying either variable renders into the real
    # app config instead of the tmp home.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)


def _desktop_cfg(home: Path) -> Path:
    from nexgen_core.paths import claude_desktop_config

    return claude_desktop_config(home)


def _home_with_desktop(tmp_path: Path, servers: dict | None) -> Path:
    # Write where the resolver reads on THIS platform (APPDATA on Windows,
    # ~/.config elsewhere): hardcoding the POSIX layout is exactly what
    # broke CI on Windows.
    from nexgen_core.paths import claude_desktop_config

    home = tmp_path / "home"
    cfg_dir = claude_desktop_config(home).parent
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if servers is not None:
        (cfg_dir / "claude_desktop_config.json").write_text(
            json.dumps({"preferences": {}, "mcpServers": servers}), encoding="utf-8")
    return home


def _vault_with_manifest(tmp_path: Path, servers: dict | None) -> Path:
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if servers is not None:
        manifest.write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    return vault


def test_no_app_config_means_no_orphans(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    vault = _vault_with_manifest(tmp_path, {"a": {"command": "x"}})
    assert desktop_app_orphans(vault, home) == []


def test_malformed_app_config_means_no_orphans(tmp_path):
    home = _home_with_desktop(tmp_path, None)
    _desktop_cfg(home).write_text("{nope", encoding="utf-8")
    vault = _vault_with_manifest(tmp_path, {"a": {"command": "x"}})
    assert desktop_app_orphans(vault, home) == []


@pytest.mark.parametrize("body", ["[]", '"just a string"', "null", "42"])
def test_non_object_app_config_means_no_orphans_not_a_crash(tmp_path, body):
    home = _home_with_desktop(tmp_path, None)
    _desktop_cfg(home).write_text(body, encoding="utf-8")
    vault = _vault_with_manifest(tmp_path, {"a": {"command": "x"}})
    assert desktop_app_orphans(vault, home) == []


def test_live_minus_manifest_is_offered_sorted(tmp_path):
    home = _home_with_desktop(tmp_path, {
        "zebra": {"command": "x"}, "alpha": {"command": "y"}, "known": {"command": "z"}})
    vault = _vault_with_manifest(tmp_path, {"known": {"command": "z"}})
    assert desktop_app_orphans(vault, home) == ["alpha", "zebra"]


def test_everything_absorbed_means_silence(tmp_path):
    home = _home_with_desktop(tmp_path, {"known": {"command": "z"}})
    vault = _vault_with_manifest(tmp_path, {"known": {"command": "z"}})
    assert desktop_app_orphans(vault, home) == []


def test_unreadable_manifest_counts_everything_live(tmp_path):
    home = _home_with_desktop(tmp_path, {"a": {"command": "x"}})
    vault = tmp_path / "vault"
    vault.mkdir()
    assert desktop_app_orphans(vault, home) == ["a"]


def test_stubs_print_reviewable_entries_and_nothing_else(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {"ui-tool": {"command": "do-thing", "args": ["--x"]}})
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["ui-tool"], home)
    out = capsys.readouterr().out
    assert "ui-tool" in out
    assert "do-thing" in out
    assert "desktop_allow" in out
    assert json.loads(_desktop_cfg(home).read_text())[
        "mcpServers"] == {"ui-tool": {"command": "do-thing", "args": ["--x"]}}


def test_rendered_managed_servers_are_not_offered_back(tmp_path, monkeypatch):
    from nexgen_core.bootstrap import desktop_app_orphans
    from nexgen_core.renderer import McpRenderer

    vault = _vault_with_manifest(tmp_path, {
        "alpha": {"transport": "stdio", "command": "/usr/bin/python3",
                  "tier": "core", "desktop_allow": True},
    })
    home = _home_with_desktop(tmp_path, {"handmade": {"command": "x"}})
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    assert renderer.render_claude_desktop(write=True)[0]
    assert desktop_app_orphans(vault, home) == ["handmade"]


def test_one_bad_entry_does_not_eat_the_good_stubs(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {
        "good": {"command": "do-thing"},
        "bad name!": {"command": "x"},
    })
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["good", "bad name!"], home)
    out = capsys.readouterr().out
    assert "good" in out and "do-thing" in out
    assert "bad name!" in out


def test_secret_shaped_env_never_reaches_the_terminal(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {
        "leaky": {"command": "do-thing", "env": {"API_KEY": "sk-live-0123456789abcdef", "MODE": "x"}},
    })
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["leaky"], home)
    out = capsys.readouterr().out
    assert "sk-live-0123456789abcdef" not in out
    assert "${API_KEY}" in out
    assert '"MODE": "x"' in out


def test_args_and_url_credentials_are_scrubbed_too(tmp_path, monkeypatch, capsys):
    home = _home_with_desktop(tmp_path, {
        "db": {"command": "do-thing",
               "args": ["postgres://analyst:s3cret-pw@db/box", "--api-key", "sk-live-abcdef0123456789"]},
    })
    monkeypatch.setattr(renderer_cli, "HOME", home)
    print_desktop_stubs(["db"], home)
    out = capsys.readouterr().out
    assert "s3cret-pw" not in out
    assert "sk-live-abcdef0123456789" not in out
    assert "postgres://<hidden>@db/box" in out
    assert "<hidden>" in out


def test_corrupt_file_between_detect_and_stubs_prints_and_stops(tmp_path, monkeypatch, capsys):
    from nexgen_core.bootstrap import desktop_app_orphans

    home = _home_with_desktop(tmp_path, {"late": {"command": "x"}})
    assert desktop_app_orphans(tmp_path / "vault", home) == ["late"]
    _desktop_cfg(home).write_text("{truncated", encoding="utf-8")
    print_desktop_stubs(["late"], home)
    assert "nothing to absorb" in capsys.readouterr().out


def test_non_required_finding_never_blocks_the_install():
    from nexgen_core import bootstrap

    absorb = bootstrap.Finding("Claude Desktop app holds 1 MCP server(s) outside the manifest: x",
                               False, required=False, remedy="absorb them")
    assert bootstrap.render([absorb], "Assistants") == 0


def test_secret_reader_keeps_bytes_and_never_asks(monkeypatch, capsys):
    from nexgen_core import bootstrap

    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda prompt="": "Sk-AbC_-.+/=è")
    monkeypatch.setattr(bootstrap, "_ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no _ask")))
    assert bootstrap._read_secret_value("value") == "Sk-AbC_-.+/=è"
    assert capsys.readouterr().out == "" and capsys.readouterr().err == ""


def test_secret_reader_eof_and_empty_mean_skip(monkeypatch):
    from nexgen_core import bootstrap

    def boom(prompt=""):
        raise EOFError

    monkeypatch.setattr(bootstrap.getpass, "getpass", boom)
    assert bootstrap._read_secret_value("value") is None
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda prompt="": "")
    assert bootstrap._read_secret_value("value") is None


def test_referenced_names_ignore_defaulted_refs():
    from nexgen_core.bootstrap import _referenced_secret_names

    adopted = {"srv": {"command": "x", "args": ["--k", "${API_KEY}"],
                       "env": {"A": "${B}", "C": "http://h:${P:-8080}/", "D": "plain"}}}
    assert _referenced_secret_names(adopted) == {"API_KEY": ["srv"], "B": ["srv"]}


def test_literal_desktop_values_become_refs_after_adoption():
    from nexgen_core.bootstrap import _adopted_entries, _referenced_secret_names

    live = {"svc": {"command": "do-thing", "env": {"GITHUB_TOKEN": "ghp_literal-value-123"}}}
    adopted = _adopted_entries(["svc"], live)
    assert set(adopted) == {"svc"}
    assert _referenced_secret_names(adopted) == {"GITHUB_TOKEN": ["svc"]}


def test_masked_servers_reach_the_closing_summary(monkeypatch, capsys):
    from nexgen_core import bootstrap

    state = _wire_script_stub(monkeypatch, stored={})
    monkeypatch.setattr(bootstrap, "_ask", lambda prompt, options="": "s")
    monkeypatch.setattr(bootstrap, "_store_script", lambda root: Path(__file__))
    bootstrap.wire_absorbed_secrets(Path("/vault"), {}, ["db-svc"])
    out = capsys.readouterr().out
    assert "db-svc" in out and "manual fix" in out
    assert state["materialized"] == 0


def test_failed_materialize_says_so(monkeypatch, capsys):
    from nexgen_core import bootstrap

    _wire_script_stub(monkeypatch, stored={"A": ("v", True)})
    import subprocess as _sp

    def boom(script):
        raise _sp.SubprocessError

    monkeypatch.setattr(bootstrap, "_store_materialize", boom)
    monkeypatch.setattr(bootstrap, "_store_script", lambda root: Path(__file__))
    bootstrap.wire_absorbed_secrets(Path("/vault"), {"A": ["s"]})
    assert "materialize failed" in capsys.readouterr().out


def test_masked_warning_survives_early_exits(monkeypatch, capsys):
    from nexgen_core import bootstrap

    monkeypatch.setattr(bootstrap, "_store_script", lambda root: Path("/nonexistent/x.py"))
    bootstrap.wire_absorbed_secrets(Path("/vault"), {}, ["db-svc"])
    out = capsys.readouterr().out
    assert "db-svc" in out and "manual fix" in out

    _wire_script_stub(monkeypatch, trial_ok=False)
    monkeypatch.setattr(bootstrap, "_store_script", lambda root: Path(__file__))
    bootstrap.wire_absorbed_secrets(Path("/vault"), {}, ["db-svc"])
    out = capsys.readouterr().out
    assert "db-svc" in out and "manual fix" in out


def test_stubs_return_the_adopted_entries_for_wiring(tmp_path, monkeypatch, capsys):
    from nexgen_core import bootstrap

    home = _home_with_desktop(tmp_path, {"svc": {"command": "do-thing", "env": {"K": "literal-abc"}}})
    adopted = bootstrap.print_desktop_stubs(["svc", "ghost"], home)
    assert set(adopted) == {"svc"}
    assert bootstrap._referenced_secret_names(adopted) == {}
    capsys.readouterr()


def _wire_script_stub(monkeypatch, stored=None, trial_ok=True):
    from nexgen_core import bootstrap

    state = {"stored": dict(stored or {}), "materialized": 0}

    def names(script):
        if not trial_ok:
            return False, set(), set(), 0
        return True, set(state["stored"]), set(), 3

    def set_(script, name, value, *, host_only):
        state["stored"][name] = (value, host_only)
        return True

    def materialize(script):
        state["materialized"] += 1
        return True

    monkeypatch.setattr(bootstrap, "_store_names", names)
    monkeypatch.setattr(bootstrap, "_store_set", set_)
    monkeypatch.setattr(bootstrap, "_store_materialize", materialize)
    return state


def test_wire_creates_only_and_summarizes(monkeypatch, capsys):
    from nexgen_core import bootstrap

    state = _wire_script_stub(monkeypatch, stored={"OLD": ("v", True)})
    answers = iter(["", "s"])
    monkeypatch.setattr(bootstrap, "_ask", lambda prompt, options="": next(answers))
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda prompt="": "N3w-V4lue")
    monkeypatch.setattr(bootstrap, "_store_script", lambda root: Path(__file__))
    bootstrap.wire_absorbed_secrets(Path("/vault"), {"NEW": ["srv"], "OLD": ["srv"], "SKIPME": ["srv"]})
    assert state["stored"]["NEW"] == ("N3w-V4lue", True)
    assert state["stored"]["OLD"] == ("v", True)
    assert "SKIPME" not in state["stored"]
    assert state["materialized"] == 1
    out = capsys.readouterr().out
    assert "NEW" in out and "SKIPME" in out


def test_wire_without_store_access_prints_names_and_writes_nothing(monkeypatch, capsys):
    from nexgen_core import bootstrap

    _wire_script_stub(monkeypatch, trial_ok=False)
    monkeypatch.setattr(bootstrap, "_store_script", lambda root: Path(__file__))
    bootstrap.wire_absorbed_secrets(Path("/vault"), {"A": ["s1"], "B": ["s2"]})
    out = capsys.readouterr().out
    assert "A" in out and "B" in out and "enroll" in out


def test_unresolved_refs_names_missing_env_only(tmp_path, monkeypatch):
    from nexgen_core.checks.mcp_checks import check_mcp_unresolved_refs
    from nexgen_core.report import Severity

    vault = _vault_with_manifest(tmp_path, {
        "a": {"command": "x", "env": {"K": "${MISSING_ONE}"}},
        "b": {"command": "x", "env": {"K": "${PRESENT:-dflt}", "J": "plain"}},
    })
    monkeypatch.setenv("PRESENT", "1")
    monkeypatch.delenv("MISSING_ONE", raising=False)
    outcome = check_mcp_unresolved_refs(vault, tmp_path / "home")
    assert outcome.severity == Severity.WARN
    assert "a: MISSING_ONE" in outcome.message
    assert "PRESENT" not in outcome.message
    monkeypatch.setenv("MISSING_ONE", "1")
    assert check_mcp_unresolved_refs(vault, tmp_path / "home").severity == Severity.OK


def test_hidden_placeholder_is_flagged(tmp_path, monkeypatch):
    from nexgen_core.checks.mcp_checks import check_mcp_unresolved_refs
    from nexgen_core.report import Severity

    vault = _vault_with_manifest(tmp_path, {
        "a": {"command": "x", "args": ["--token", "<hidden>"]},
    })
    outcome = check_mcp_unresolved_refs(vault, tmp_path / "home")
    assert outcome.severity == Severity.WARN
    assert "<hidden>" in outcome.message


def test_masked_only_skips_pointless_materialize(monkeypatch, capsys):
    from nexgen_core import bootstrap

    state = _wire_script_stub(monkeypatch, stored={})
    monkeypatch.setattr(bootstrap, "_store_script", lambda root: Path(__file__))
    bootstrap.wire_absorbed_secrets(Path("/vault"), {}, ["db-svc"])
    out = capsys.readouterr().out
    assert "db-svc" in out and "manual fix" in out
    assert "already stored" not in out
    assert state["materialized"] == 0
