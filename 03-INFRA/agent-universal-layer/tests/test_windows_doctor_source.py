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


def test_windows_doctor_derives_layer_from_split_topology_vault_data():
    """Regression: $Layer used to be derived straight from $Vault (only
    KNOWLEDGE_VAULT_PATH), so in a split topology (AGENT_VAULT_DATA pointing
    somewhere else, exactly what agent-doctor.sh's own VAULT_DATA fallback
    already supports) it silently resolved to a path that doesn't exist --
    turning the permissions-manifest guardrail check and the BUG-B
    require_env check into silent no-ops instead of failing loudly."""
    source = DOCTOR.read_text(encoding="utf-8")

    assert (
        '$VaultData = if ($env:AGENT_VAULT_DATA) { $env:AGENT_VAULT_DATA } '
        'elseif ($env:KNOWLEDGE_VAULT_PATH) { $env:KNOWLEDGE_VAULT_PATH } '
        'else { Join-Path $HomeDir "KnowledgeVault" }'
    ) in source
    assert '$Layer   = Join-Path $VaultData "03-INFRA\\agent-universal-layer"' in source
    assert '$Layer   = Join-Path $Vault "03-INFRA\\agent-universal-layer"' not in source
    # $Vault itself stays anchored to KNOWLEDGE_VAULT_PATH alone: the S1
    # git-repo-health checks operate on the vault's own git working tree, a
    # different concern from where its 03-INFRA content lives.
    assert '$Vault   = if ($env:KNOWLEDGE_VAULT_PATH) { $env:KNOWLEDGE_VAULT_PATH } else { Join-Path $HomeDir "KnowledgeVault" }' in source
    # Every $Layer-derived check must keep resolving through the fixed
    # variable, not bypass it.
    for needle in (
        '$PermManifest = Join-Path $Layer "permissions\\manifest.yaml"',
        '$ManifestYaml = Join-Path $Layer "mcp\\manifest.yaml"',
    ):
        assert needle in source


def test_windows_doctor_surfaces_path_limit_and_legacy_skill_migration():
    source = DOCTOR.read_text(encoding="utf-8")

    assert "8191-character inherited-variable limit" in source
    assert "--migrate-legacy" in source
    assert "legacy eager skill view(s) await explicit quarantine" in source


def test_windows_doctor_supports_current_opencode_jsonc_and_user_install_path():
    source = DOCTOR.read_text(encoding="utf-8")

    assert "opencode.jsonc" in source
    assert "Test-NexgenJsonc" in source
    assert "Get-NexgenJsoncInstructions" in source
    assert '".opencode\\bin\\opencode.exe"' in source


def test_windows_doctor_honors_live_consumer_sandbox_gate():
    source = DOCTOR.read_text(encoding="utf-8")

    assert "NEXGEN_SKIP_LIVE_CONSUMER_PROBES" in source
    assert "Antigravity behavioral probe skipped by the sandbox safety gate" in source
    assert "OpenCode consumer test skipped by the sandbox safety gate" in source


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
    # ReadAllText WITHOUT an explicit encoding: on .NET Framework this means
    # "UTF-8, but honour the BOM if present" -- the same rule the script
    # runtime itself applies to the file on disk. An explicit utf-8 read
    # would NOT reproduce what PowerShell 5.1 does to a BOM-less file
    # (which it decodes as ANSI/cp1252), so never add an encoding argument.
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


def test_every_repo_ps1_is_ascii_pure_or_utf8_bom():
    """Regression (v0.98.0-v0.98.3): agent-doctor.ps1 was UTF-8 without BOM.
    Windows PowerShell 5.1 decodes a BOM-less .ps1 as ANSI/cp1252, where the
    bytes of an em-dash (E2 80 94) spell a smart closing quote that the
    parser treats as a string delimiter -- the file then failed to parse
    ('Token '}' unexpected') and nexgen-update's pre-upgrade doctor step
    died on every Windows machine. The Linux pwsh-7 lint job never saw it.
    Any .ps1 must therefore be pure ASCII or carry a UTF-8 BOM, so the
    cp1252 decoding can never derail the parser. Cross-platform byte check:
    it must fail on the broken files even on a runner without PowerShell."""
    repo = Path(__file__).resolve().parents[3]
    ps1_files = sorted(
        path
        for path in repo.rglob("*.ps1")
        if not any(part in (".git", ".venv", "node_modules") for part in path.parts)
    )
    assert ps1_files, "no .ps1 files found -- the sweep silently shrank to nothing"

    offenders = []
    for path in ps1_files:
        raw = path.read_bytes()
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        payload = raw[3:] if has_bom else raw
        if any(byte > 0x7F for byte in payload):
            offenders.append(path.relative_to(repo).as_posix())
    assert not offenders, (
        "these .ps1 files are neither pure ASCII nor UTF-8-with-BOM, so "
        "Windows PowerShell 5.1 (ANSI/cp1252 decode of BOM-less files) can "
        "misparse them:\n" + "\n".join(f"  - {name}" for name in offenders)
    )
