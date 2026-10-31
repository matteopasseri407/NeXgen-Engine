"""Tests for turning a machine into a node from the CLI.

What is proved here is the order that failed in the field on 2026-10-26: a node
started beside a live local gateway crash-loops, because the local token wins
over the pairing token and both poll the same chat channel. So the tests check
that the retirement happens first, that it touches only the three keys it owns,
and that the enrolment uses the documented commands.

Every command is a recorder. No real service, no real OpenClaw, and nothing
outside `tmp_path` is read or written.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import node_retire  # noqa: E402
from nexgen_core.openclaw.node_apply import apply_node  # noqa: E402
from nexgen_core.openclaw.node_retire import (  # noqa: E402
    RetireResult,
    archive_drop_in,
    describe_config_change,
    prepare_config,
    report,
)
from nexgen_core.openclaw_service import RunResult  # noqa: E402

#: The profile the engine adds on a node: the shared Chrome keeps its port, OpenClaw only attaches.
NEXGEN_PROFILE = {"cdpUrl": "http://127.0.0.1:9222", "attachOnly": True}


@pytest.fixture(autouse=True)
def _shared_chrome_absent(monkeypatch):
    """Whether this machine runs the shared Chrome is a fact about the host, not about the test.

    Pinned to "not set up" for every case here, so the file's expectations do not
    change with the machine the suite runs on; the browser cases set it themselves.
    """
    monkeypatch.setattr(node_retire, "shared_chrome_ready", lambda *_args, **_kwargs: False)


STOPPED_NODE = {
    "service": {"label": "systemd user", "loaded": False, "command": None, "runtime": {"status": "stopped"}},
}

NODE_ELSEWHERE = {
    "service": {
        "label": "systemd user",
        "loaded": True,
        "command": {"programArguments": ["openclaw", "node", "run", "--host", "other.tail.ts.net", "--port", "443", "--tls"]},
        "runtime": {"status": "running"},
    },
}


class RecordingRunner:
    """Records every command and answers from canned output."""

    def __init__(self, node_status: dict | None = None, code: int = 0):
        self.calls: list[list[str]] = []
        self.node_status = node_status if node_status is not None else STOPPED_NODE
        self.code = code

    def __call__(self, argv: list[str], timeout: float | None = None) -> RunResult:
        self.calls.append(list(argv))
        if argv[:3] == ["openclaw", "node", "status"]:
            return RunResult(0, json.dumps(self.node_status), "")
        return RunResult(self.code, "", "")

    def issued(self, *argv: str) -> bool:
        return list(argv) in self.calls


def _config(home: Path, document: dict) -> Path:
    path = home / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _gateway_document() -> dict:
    """A machine that still believes it is a gateway: token, channel, no session hosting."""
    return {
        "gateway": {"bind": "loopback", "mode": "local", "port": 18789, "auth": {"mode": "token", "token": "x" * 48}},
        "channels": {
            "telegram": {"enabled": True, "dmPolicy": "allowlist"},
            "slack": {"enabled": False},
        },
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"vault-library": {"command": "vault-mcp"}}},
    }


def test_prepare_config_puts_the_file_in_node_state(tmp_path: Path) -> None:
    path = _config(tmp_path, _gateway_document())

    change = prepare_config(path, out=lambda _line: None)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert change.auth_removed is True
    assert change.channels_disabled == ("telegram",)
    assert change.session_hosting_enabled is True
    assert "auth" not in document["gateway"]
    assert document["gateway"]["bind"] == "loopback"
    assert document["channels"]["telegram"]["enabled"] is False
    assert document["channels"]["telegram"]["dmPolicy"] == "allowlist"
    assert document["channels"]["slack"]["enabled"] is False
    assert document["nodeHost"]["workerRuns"]["enabled"] is True
    assert document["agents"] == {"entries": {"main": {}}}
    assert document["mcp"]["servers"]["vault-library"] == {"command": "vault-mcp"}
    assert change.backup is not None and change.backup.is_file()


def test_prepare_config_is_idempotent(tmp_path: Path) -> None:
    path = _config(tmp_path, _gateway_document())
    prepare_config(path, out=lambda _line: None)
    before = path.read_text(encoding="utf-8")

    again = prepare_config(path, out=lambda _line: None)

    assert again.auth_removed is False
    assert again.channels_disabled == ()
    assert again.session_hosting_enabled is False
    assert again.backup is None
    assert path.read_text(encoding="utf-8") == before


def test_prepare_config_leaves_a_missing_file_alone(tmp_path: Path) -> None:
    path = tmp_path / ".openclaw" / "openclaw.json"

    change = prepare_config(path, out=lambda _line: None)

    assert change.auth_removed is False
    assert change.session_hosting_enabled is False
    assert not path.exists()


def test_archive_drop_in_moves_the_leftover_directory(tmp_path: Path) -> None:
    drop_in = tmp_path / ".config" / "systemd" / "user" / "openclaw-gateway.service.d"
    drop_in.mkdir(parents=True)
    (drop_in / "override.conf").write_text("[Service]\nMemoryMax=2G\n", encoding="utf-8")

    archived = archive_drop_in(home=tmp_path, out=lambda _line: None)

    assert archived is not None and archived.is_dir()
    assert (archived / "override.conf").is_file()
    assert not drop_in.exists()
    assert archived.parent == tmp_path / ".openclaw" / "backups"


def test_archive_drop_in_does_nothing_when_there_is_none(tmp_path: Path) -> None:
    assert archive_drop_in(home=tmp_path, out=lambda _line: None) is None


def test_describe_config_change_reads_without_writing(tmp_path: Path) -> None:
    path = _config(tmp_path, _gateway_document())
    before = path.read_text(encoding="utf-8")

    lines = describe_config_change(path)

    assert path.read_text(encoding="utf-8") == before
    # The three changes of the document, and the hint that the shared Chrome is not here.
    assert len(lines) == 4
    assert all(line.startswith("- ") for line in lines)


def test_prepare_config_adds_the_profile_of_the_shared_chrome(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(node_retire, "shared_chrome_ready", lambda *_args, **_kwargs: True)
    path = _config(tmp_path, _gateway_document())
    printed: list[str] = []

    prepare_config(path, out=printed.append)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["browser"] == {"profiles": {"nexgen": NEXGEN_PROFILE}, "defaultProfile": "nexgen"}
    # The rest of the node state is written by the same pass: one write, not two.
    assert "auth" not in document["gateway"]
    assert document["nodeHost"]["workerRuns"]["enabled"] is True
    assert len(printed) >= 1


def test_prepare_config_changes_no_browser_without_the_shared_chrome(tmp_path: Path) -> None:
    path = _config(tmp_path, _gateway_document())
    printed: list[str] = []

    change = prepare_config(path, out=printed.append)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert "browser" not in document
    assert change.auth_removed is True
    # The one hint line, and it is the only thing said about the browser.
    assert len([line for line in printed if "agent-chrome" in line or "Chrome" in line]) == 1


def test_prepare_config_keeps_the_default_profile_the_person_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(node_retire, "shared_chrome_ready", lambda *_args, **_kwargs: True)
    document = _gateway_document() | {"browser": {"defaultProfile": "work"}}
    path = _config(tmp_path, document)
    printed: list[str] = []

    prepare_config(path, out=printed.append)

    browser = json.loads(path.read_text(encoding="utf-8"))["browser"]
    assert browser["defaultProfile"] == "work"
    assert browser["profiles"] == {"nexgen": NEXGEN_PROFILE}
    assert any("defaultProfile" in line for line in printed)


def test_prepare_config_leaves_a_nexgen_profile_with_other_values_alone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(node_retire, "shared_chrome_ready", lambda *_args, **_kwargs: True)
    theirs = {"cdpUrl": "http://127.0.0.1:9555", "attachOnly": True}
    document = _gateway_document() | {"browser": {"profiles": {"nexgen": theirs}}}
    path = _config(tmp_path, document)
    printed: list[str] = []

    prepare_config(path, out=printed.append)

    browser = json.loads(path.read_text(encoding="utf-8"))["browser"]
    assert browser["profiles"]["nexgen"] == theirs
    assert any("nexgen" in line for line in printed)


def test_a_browser_already_in_place_is_not_written_again(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(node_retire, "shared_chrome_ready", lambda *_args, **_kwargs: True)
    document = _gateway_document() | {
        "browser": {"defaultProfile": "nexgen", "profiles": {"nexgen": NEXGEN_PROFILE}},
    }
    path = _config(tmp_path, document)
    prepare_config(path, out=lambda _line: None)
    before = path.read_text(encoding="utf-8")

    again = prepare_config(path, out=lambda _line: None)

    assert again.backup is None
    assert path.read_text(encoding="utf-8") == before


def test_a_person_default_with_the_profile_in_place_is_not_rewritten(tmp_path: Path, monkeypatch) -> None:
    """The line saying the default stays describes an addition: with nothing added, nothing is written."""
    monkeypatch.setattr(node_retire, "shared_chrome_ready", lambda *_args, **_kwargs: True)
    document = _gateway_document() | {
        "browser": {"defaultProfile": "work", "profiles": {"nexgen": NEXGEN_PROFILE}},
    }
    path = _config(tmp_path, document)
    prepare_config(path, out=lambda _line: None)
    before = path.read_text(encoding="utf-8")

    again = prepare_config(path, out=lambda _line: None)

    assert again.browser_profile_added is False
    assert again.backup is None
    assert path.read_text(encoding="utf-8") == before


def test_the_summary_names_the_browser_when_it_is_the_only_change(tmp_path: Path, monkeypatch) -> None:
    """A node already in node state that only gained the profile must not read as untouched."""
    monkeypatch.setattr(node_retire, "shared_chrome_ready", lambda *_args, **_kwargs: True)
    document = {
        "gateway": {"bind": "loopback", "mode": "local", "port": 18789},
        "channels": {"telegram": {"enabled": False}},
        "nodeHost": {"workerRuns": {"enabled": True}},
    }
    path = _config(tmp_path, document)

    change = prepare_config(path, out=lambda _line: None)

    assert change.browser_profile_added is True
    assert "browser" in report(RetireResult(None, change))


def test_apply_node_dry_run_touches_nothing(tmp_path: Path) -> None:
    path = _config(tmp_path, _gateway_document())
    before = path.read_text(encoding="utf-8")
    runner = RecordingRunner()
    printed: list[str] = []

    code = apply_node("central.tail.ts.net", name="desktop", dry_run=True, home=tmp_path,
                      platform="linux", runner=runner, systemd_user_dir=tmp_path, out=printed.append)

    assert code == 0
    assert path.read_text(encoding="utf-8") == before
    assert not runner.issued("openclaw", "gateway", "stop")
    assert not runner.issued("openclaw", "connect")
    assert any("https://central.tail.ts.net" in line for line in printed)


def test_apply_node_retires_the_local_gateway_before_enrolling(tmp_path: Path) -> None:
    path = _config(tmp_path, _gateway_document())
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    drop_in = unit_dir / "openclaw-gateway.service.d"
    drop_in.mkdir(parents=True)
    runner = RecordingRunner()
    probes: list[bool] = []

    code = apply_node(
        "central.tail.ts.net",
        name="desktop",
        join_target="https://central.tail.ts.net/j/one-shot",
        home=tmp_path,
        platform="linux",
        runner=runner,
        systemd_user_dir=unit_dir,
        wait_probe_fn=lambda: probes.append(True) or True,
        out=lambda _line: None,
    )

    assert code == 0
    assert runner.issued("openclaw", "gateway", "stop")
    assert runner.issued("openclaw", "gateway", "uninstall")
    connect = [call for call in runner.calls if call[:2] == ["openclaw", "connect"]]
    assert len(connect) == 1
    assert "--service" in connect[0] and "--session-host" in connect[0] and "--target-file" in connect[0]
    assert runner.calls.index(["openclaw", "gateway", "stop"]) < runner.calls.index(connect[0])
    assert probes == [True]

    document = json.loads(path.read_text(encoding="utf-8"))
    assert "auth" not in document["gateway"]
    assert document["channels"]["telegram"]["enabled"] is False
    assert document["nodeHost"]["workerRuns"]["enabled"] is True
    assert not drop_in.exists()


def test_apply_node_refuses_another_gateway_without_force(tmp_path: Path) -> None:
    _config(tmp_path, _gateway_document())
    runner = RecordingRunner(node_status=NODE_ELSEWHERE)

    code = apply_node("central.tail.ts.net", name="desktop", home=tmp_path, platform="linux",
                      runner=runner, systemd_user_dir=tmp_path, out=lambda _line: None)

    assert code == 1
    assert not runner.issued("openclaw", "gateway", "stop")
    assert not runner.issued("openclaw", "connect")


def test_apply_node_without_join_installs_with_tls_for_a_tailnet_name(tmp_path: Path) -> None:
    _config(tmp_path, _gateway_document())
    runner = RecordingRunner()

    code = apply_node("central.tail.ts.net", name="desktop", home=tmp_path, platform="linux",
                      runner=runner, systemd_user_dir=tmp_path, out=lambda _line: None)

    assert code == 0
    install = [call for call in runner.calls if call[:3] == ["openclaw", "node", "install"]]
    assert len(install) == 1
    assert install[0] == [
        "openclaw", "node", "install", "--host", "central.tail.ts.net", "--port", "443", "--tls",
        "--display-name", "desktop",
    ]
    assert runner.issued("openclaw", "node", "start")
