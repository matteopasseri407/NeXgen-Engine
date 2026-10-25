"""The flags that turn a PC into a node reach the role, on both entry points.

One job: pin the two hops between what the owner types and what the node role
does. `--join`, `--force` and `--check` are the words; `install.ps1` is the only
way to type them on Windows, and `bootstrap.py` is the only thing that turns
them into a call. A flag that stops one hop short is a command that looks right
and does nothing, which is worse than a missing one because nobody retypes it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

JOIN_URL = "https://gw.test:18789/j/one-time-code"


def test_bootstrap_hands_the_node_flags_to_the_role(monkeypatch, tmp_path: Path) -> None:
    """`--join`, `--force`, `--check` and `--node-name` arrive as the role's own arguments."""
    from nexgen_core import bootstrap

    seen: dict = {}

    def fake_run_node(root, **kwargs):
        seen["root"] = root
        seen["kwargs"] = kwargs
        return 0

    # The dispatch is the subject. Everything before it is "this machine is fine",
    # so the test cannot fail over a missing prerequisite on the machine it runs on.
    def nothing_found(*_args, **_kwargs) -> list:
        return []

    monkeypatch.setattr(bootstrap, "preflight", nothing_found)
    monkeypatch.setattr(bootstrap, "scaffold", nothing_found)
    monkeypatch.setattr(bootstrap, "detect_clis", lambda: ["claude"])
    monkeypatch.setattr(bootstrap, "desktop_app_orphans", nothing_found)
    monkeypatch.setattr("nexgen_core.roles.run_node_role", fake_run_node)

    code = bootstrap.main([
        "--role", "node",
        "--check",
        "--force",
        "--join", JOIN_URL,
        "--node-name", "edge-pc",
        "--root", str(tmp_path),
    ])

    assert code == 0
    assert seen["root"] == tmp_path
    assert seen["kwargs"]["join"] == JOIN_URL
    assert seen["kwargs"]["check"] is True
    assert seen["kwargs"]["force"] is True
    assert seen["kwargs"]["node_name"] == "edge-pc"


def test_install_ps1_forwards_the_node_flags_to_bootstrap(monkeypatch, tmp_path: Path) -> None:
    """`-Join`, `-Force`, `-NodeName` become `--join`, `--force`, `--node-name`.

    install.ps1 keeps no logic on purpose, so the only honest proof is to run it:
    a recording `python` first on PATH takes the handoff and keeps the argv, and
    no engine, gateway or user file is involved.
    """
    if sys.platform != "win32":
        pytest.skip("install.ps1 is the Windows launcher")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "argv.txt"
    (bin_dir / "python.cmd").write_text(
        '@echo off\r\necho "%*" >> "%NEXGEN_ARGV_RECORD%"\r\nexit /b 0\r\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("NEXGEN_ARGV_RECORD", str(record))

    installer = Path(__file__).resolve().parents[3] / "install.ps1"
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(installer),
            "-Role", "node", "-Join", JOIN_URL, "-Force", "-NodeName", "edge-pc",
        ],
        capture_output=True, text=True, timeout=180, check=False, cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    forwarded = record.read_text(encoding="utf-8").splitlines()[-1]
    assert "--role node" in forwarded
    assert f"--join {JOIN_URL}" in forwarded
    assert "--node-name edge-pc" in forwarded
    assert "--force" in forwarded
