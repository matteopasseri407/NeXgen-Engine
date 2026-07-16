"""Windows doctor checks that must not disappear behind POSIX-only skips."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
DOCTOR = REPO / "03-INFRA" / "scripts" / "agent-doctor.ps1"
SYNC_LAUNCHER = REPO / "03-INFRA" / "scripts" / "agent-sync.ps1"


def test_windows_doctor_resolves_engine_owned_helpers_from_its_own_checkout():
    source = DOCTOR.read_text(encoding="utf-8")

    assert '$EngineInfra = Split-Path -Parent $PSScriptRoot' in source
    assert '$RenderPy = Join-Path $EngineInfra "agent-universal-layer\\mcp\\render.py"' in source
    assert '$skillsSyncScript = Join-Path $PSScriptRoot "skills-sync.py"' in source
    assert 'Join-Path $Layer "mcp\\render.py"' not in source
    assert '$Layer\\mcp\\render.py' not in source
    assert "function Resolve-NexgenPython" in source
    assert 'import sys, yaml' in source
    assert 'sys.version_info >= (3, 10)' in source
    assert '$renderOut = & $NexgenPythonCommand @NexgenPythonPrefix $RenderPy' in source
    assert '$renderOut = python $RenderPy' not in source
    assert source.count('$RenderPy = Join-Path $EngineInfra') == 1
    assert 'Join-Path $Vault "03-INFRA\\scripts\\skills-sync.py"' not in source
    assert '[IO.File]::ReadAllText($AgGlobal)' in source
    assert '(Get-Item -LiteralPath $AgGlobal).Length' not in source


def test_windows_doctor_surfaces_path_limit_and_legacy_skill_migration():
    source = DOCTOR.read_text(encoding="utf-8")

    assert "8191-character inherited-variable limit" in source
    assert "--migrate-legacy" in source
    assert "legacy eager skill view(s) await explicit quarantine" in source


def test_windows_sync_launcher_uses_only_a_runtime_with_pyyaml():
    source = SYNC_LAUNCHER.read_text(encoding="utf-8")

    assert 'foreach ($name in @("python3", "python"))' in source
    assert 'import sys, yaml' in source
    assert 'sys.version_info >= (3, 10)' in source
    assert 'Prefix = @("-3")' in source
    assert '& $runtimeCommand @runtimePrefix $script @args' in source
    assert 'if ($py) { & $py -3 $script @args }' not in source


@pytest.mark.skipif(os.name != "nt", reason="Windows candidate behavior requires cmd.exe and PowerShell.")
def test_windows_sync_launcher_rejects_a_failed_first_python_candidate(tmp_path):
    rejected_log = tmp_path / "rejected-python.txt"
    (tmp_path / "python3.cmd").write_text(
        f'@echo off\r\necho rejected>"{rejected_log}"\r\nexit /b 1\r\n',
        encoding="utf-8",
    )
    (tmp_path / "python.cmd").write_text(
        f'@echo off\r\n"{sys.executable}" %*\r\nexit /b %ERRORLEVEL%\r\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    system_root = env.get("SystemRoot") or env.get("SYSTEMROOT") or r"C:\Windows"
    env["PATH"] = os.pathsep.join([str(tmp_path), str(Path(system_root) / "System32")])

    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(SYNC_LAUNCHER), "--help",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert rejected_log.exists()
    assert "agent_sync modes:" in result.stdout.lower()


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser check is Windows-only.")
@pytest.mark.parametrize("script", [DOCTOR, SYNC_LAUNCHER])
def test_windows_control_scripts_parse_in_windows_powershell(script):
    command = (
        "[void][scriptblock]::Create([IO.File]::ReadAllText("
        + repr(str(script))
        + "))"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
