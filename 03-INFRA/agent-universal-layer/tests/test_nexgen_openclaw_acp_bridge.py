"""The ACP bridge launches the OpenCode that is installed, not npx's old V1.

Found by the first real setup: acpx's default for `opencode` is
`npx -y opencode-ai acp`, the V1 package, which exits at once on a V2
`opencode.json`. The installed OpenCode speaks ACP itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.acp import acpx_entry  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login, opencode_command  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.setup_write import merged_document  # noqa: E402 - sys.path shim for cloned checkout


def _detection(command: str) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path="x", config_present=True),
        claude=Login(binary=False, login=None), codex=Login(binary=False, login=None),
        opencode_go=True, local=LocalModels(), n8n=False, opencode=bool(command), opencode_command=command,
    )


def test_the_npm_shim_resolves_to_the_real_executable(tmp_path: Path) -> None:
    shim = tmp_path / "npm" / "opencode.cmd"
    exe = tmp_path / "npm" / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe"
    exe.parent.mkdir(parents=True)
    shim.write_text("@echo off", encoding="utf-8")
    exe.write_bytes(b"")
    assert opencode_command(str(shim)) == str(exe)


def test_a_plain_executable_is_kept_and_nothing_is_nothing(tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "opencode.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    assert opencode_command(str(binary)) == str(binary)
    assert opencode_command(None) == ""


def test_the_bridge_launches_the_installed_opencode_with_acp() -> None:
    entry = acpx_entry(_detection(r"C:\npm\node_modules\@opencode\cli\bin\opencode.exe"))
    assert entry["config"]["agents"]["opencode"] == {
        "command": r"C:\npm\node_modules\@opencode\cli\bin\opencode.exe", "args": ["acp"],
    }
    assert entry["config"]["permissionMode"] == "approve-all"


def test_without_opencode_the_bridge_keeps_its_own_defaults() -> None:
    assert "agents" not in acpx_entry(_detection(""))["config"]
    assert "agents" not in acpx_entry()["config"]


OLD_RUNTIME = {"type": "acp", "acp": {"agent": "opencode", "backend": "acpx", "mode": "persistent"}}


def test_the_acp_runtime_an_earlier_setup_wrote_is_retired() -> None:
    current = {"agents": {"entries": {"sviluppo": {"model": {"primary": "x"}, "runtime": dict(OLD_RUNTIME)}}}}
    proposed = {"agents": {"entries": {"sviluppo": {"model": "opencode-go/deepseek-v4.1-flash"}}}}
    merged = merged_document(current, proposed)
    assert merged["agents"]["entries"]["sviluppo"] == {"model": "opencode-go/deepseek-v4.1-flash"}


def test_a_runtime_the_person_changed_by_hand_stays() -> None:
    mine = {"type": "acp", "acp": {"agent": "opencode", "backend": "acpx", "mode": "oneshot"}}
    current = {"agents": {"entries": {"sviluppo": {"runtime": mine}}}}
    proposed = {"agents": {"entries": {"sviluppo": {"model": "opencode-go/deepseek-v4.1-flash"}}}}
    assert merged_document(current, proposed)["agents"]["entries"]["sviluppo"]["runtime"] == mine
