"""A Node the administrator installed does not stop the gateway account's install.

Seen on a clean Ubuntu 24.04 with Node in /usr/local: the setup stopped at step
1/9 asking the person to run `npm config set prefix ~/.local`. The setup now does
it, and only for the prefix npm chose by itself.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import npm_prefix  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_service import RunResult  # noqa: E402 - sys.path shim for cloned checkout


def _system_node(tmp_path: Path) -> tuple[Path, str]:
    """`<prefix>/bin/npm` in a folder this account cannot write, a link into npm's package as Node ships it."""
    prefix = tmp_path / "usr-local"
    cli = prefix / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    (prefix / "bin").mkdir()
    npm = prefix / "bin" / "npm"
    npm.symlink_to(cli)
    return prefix, str(npm)


def _npm(prefix: Path, calls: list[list[str]], *, set_code: int = 0):
    def run(argv, env=None):
        calls.append(list(argv))
        if argv[1:] == ["config", "get", "prefix"]:
            return RunResult(0, f"{prefix}\n", "")
        return RunResult(set_code, "", "")
    return run


def _readonly(monkeypatch, *folders: Path) -> None:
    locked = {str(folder) for folder in folders}
    monkeypatch.setattr(npm_prefix.host_preflight, "is_writable", lambda path: str(path) not in locked)


def test_the_default_prefix_moves_to_the_home_and_its_bin_leads_the_path(tmp_path, monkeypatch):
    i18n.set_language("en")
    monkeypatch.setenv("PATH", "/usr/bin")
    prefix, npm = _system_node(tmp_path)
    _readonly(monkeypatch, prefix)
    calls: list[list[str]] = []
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin"}
    lines: list[str] = []

    assert npm_prefix.ensure_user_prefix(_npm(prefix, calls), npm, env, lines.append) is True

    target = tmp_path / "home" / ".local"
    assert [npm, "config", "set", "prefix", str(target)] in calls
    assert env["PATH"].split(os.pathsep)[0] == str(target / "bin")
    # The gateway service and the doctor of this same install look `openclaw` up here.
    assert os.environ["PATH"].split(os.pathsep)[0] == str(target / "bin")
    assert str(target) in lines[0]


def test_a_prefix_someone_set_is_left_alone_and_the_setup_stops(tmp_path, monkeypatch):
    i18n.set_language("en")
    _, npm = _system_node(tmp_path)
    chosen = tmp_path / "shared-prefix"
    _readonly(monkeypatch, chosen)
    calls: list[list[str]] = []
    lines: list[str] = []

    ok = npm_prefix.ensure_user_prefix(_npm(chosen, calls), npm, {"HOME": str(tmp_path)}, lines.append)

    assert ok is False
    assert not any(argv[1:3] == ["config", "set"] for argv in calls)
    assert "npm config set prefix ~/.local" in lines[-1]


def test_a_writable_prefix_changes_nothing(tmp_path, monkeypatch):
    prefix, npm = _system_node(tmp_path)
    _readonly(monkeypatch)
    calls: list[list[str]] = []
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin"}

    assert npm_prefix.ensure_user_prefix(_npm(prefix, calls), npm, env, lambda _l: None) is True
    assert calls == [[npm, "config", "get", "prefix"]]
    assert env["PATH"] == "/usr/bin"


def test_a_refused_config_write_stops_with_the_manual_step(tmp_path, monkeypatch):
    i18n.set_language("en")
    prefix, npm = _system_node(tmp_path)
    _readonly(monkeypatch, prefix)
    lines: list[str] = []

    ok = npm_prefix.ensure_user_prefix(_npm(prefix, [], set_code=1), npm, {"HOME": str(tmp_path)}, lines.append)

    assert ok is False
    assert "cannot write" in lines[0]


def test_a_program_that_is_not_there_is_an_answer_not_a_traceback(monkeypatch):
    """The gateway service step crashed the whole install with FileNotFoundError: 'openclaw'."""
    from nexgen_core.openclaw_processes import default_runner

    monkeypatch.setenv("PATH", "")
    result = default_runner(["nexgen-no-such-program-anywhere", "gateway", "install"])

    assert result.returncode == 127
    assert "nexgen-no-such-program-anywhere" in result.stderr
