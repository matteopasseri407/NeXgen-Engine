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


def test_windows_doctor_parses_a_real_opencode_json_and_derives_vault_url(sandbox, monkeypatch):
    """Regression (2026-08-16): agent-doctor.ps1 FAILed on a HEALTHY Windows
    host for two PowerShell-5.1-only reasons. (1) The inline python -c code
    used double quotes for encoding="utf-8"/"instructions"; PS 5.1 strips
    double quotes when it passes an argument to a native exe, so Python
    received encoding=utf-8 and the doctor reported "opencode.json: invalid
    JSON/JSONC" on a perfectly valid file. (2) The render.py call was piped
    into Select-Object -First 1; PS 5.1 closes the pipe as soon as the first
    line arrives, which kills the child before it reports its exit code
    ($LASTEXITCODE = -1, reproduced 19/20 runs live), so the real
    vault-library URL was flagged as "cannot be derived from the rendered
    manifest". A valid opencode.json and a derivable endpoint must now read
    as OK, not FAIL.

    The runner never installs opencode, so the doctor's presence probe must
    be stubbed (the sandbox already prepends bin_stubs to PATH) for the
    OpenCode sections to be exercised at all; and VAULT_LIBRARY_URL must be
    set via the environment the doctor actually receives (run_agent_doctor
    rebuilds sandbox.env() itself, a local `env` variable would be lost)."""
    from conftest import run_agent_doctor

    opencode_stub = sandbox.bin_stubs / "opencode"
    opencode_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    opencode_stub.chmod(0o755)

    config = sandbox.home / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{\n'
        '  "instructions": [\n'
        '    "~/KnowledgeVault/03-INFRA/agent-universal-layer/instructions/AGENTS.md"\n'
        '  ]\n'
        '}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VAULT_LIBRARY_URL", "http://127.0.0.1:55555/mcp")
    monkeypatch.setenv("VAULT_LIBRARY_TOKEN", "fake-token")
    result = run_agent_doctor(sandbox)

    # The sandbox deliberately has other real FAILs (no vault git repo,
    # Claude/Codex/Antigravity not configured): what this regression locks
    # in is that the two PS-5.1 bugs no longer produce their FAIL lines.
    assert "opencode.json: invalid JSON/JSONC" not in result.stdout
    assert "cannot be inspected because" not in result.stdout
    assert "vault-library endpoint cannot be derived" not in result.stdout
    assert "opencode.json: valid JSON" in result.stdout, (
        "the OpenCode config section must actually run and read the file:\n" + result.stdout
    )
    assert "vault-library: " in result.stdout


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


def test_no_powershell_51_native_call_antipatterns_in_engine_ps1_files():
    """Lint for the CLASS behind the two 2026-08-16 doctor bugs, so the
    instance fix never silently regresses into a sibling occurrence:

    1. Inline python `-c` code passed through PowerShell 5.1 must never use
       double quotes: PS 5.1 strips them when handing the argument to a
       native exe, so the python source mutates (encoding="utf-8" becomes
       encoding=utf-8 -> NameError) and a VALID opencode.json was reported
       as invalid JSON. Inline python is single-quoted at the PS layer, so
       a literal double quote inside it is exactly the bug pattern.
    2. A native call piped straight into `Select-Object -First 1` loses the
       child's exit code: PS 5.1 closes the pipe after the first line and
       kills the child before it reports ($LASTEXITCODE = -1). render.py's
       vault-library URL derivation then FAILed on a healthy host (19/20
       runs reproduced it). The safe form captures the full output into a
       variable first and only then takes the first line.

    Select-Object -First 1 over PS-native cmdlets (Select-String output) or
    over an ALREADY-captured variable is fine -- the lint only rejects a
    native call (`& ` or a bare executable token) piped directly into it.
    git's exit code is read from the preceding `fetch ... | Out-Null` line,
    which is not truncated, so those tag-list lines are explicitly allowed.
    """
    repo = Path(__file__).resolve().parents[3]
    doctor = repo / "03-INFRA/scripts/agent-doctor.ps1"
    source = doctor.read_text(encoding="utf-8")
    lines = source.splitlines()

    # 1. inline python -c with a double quote inside the single-quoted code
    for i, line in enumerate(lines, 1):
        if " -c " in line and "'" in line and '"' in line:
            raise AssertionError(
                f"agent-doctor.ps1:{i} passes inline python -c containing a "
                "double quote -- PS 5.1 strips it when calling the native exe "
                f"(2026-08-16 bug 1): {line.strip()}"
            )

    # 2. native call piped directly into Select-Object -First 1
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if "| Select-Object -First 1" not in stripped:
            continue
        if stripped.startswith("("):
            stripped = stripped[1:].lstrip()
        if stripped.startswith("& ") or stripped.startswith("$latestTag = (& git"):
            if "$latestTag" in stripped and "git -C" in stripped:
                continue  # allowed: exit code comes from the fetch line above
            raise AssertionError(
                f"agent-doctor.ps1:{i} pipes a native call directly into "
                "Select-Object -First 1 -- PS 5.1 kills the child before it "
                "reports its exit code ($LASTEXITCODE = -1). Capture the full "
                f"output into a variable first (2026-08-16 bug 2): {stripped}"
            )
        if "$renderedVaultLines" in stripped or "$codexVerRaw" in stripped or "$modeLine" in stripped or "$oldest_ts" in stripped:
            continue  # variable already captured, or PS-native Select-String output
        if stripped.startswith("$") and "Lines | Select-Object -First 1" in stripped:
            continue  # the safe form: native output captured fully, THEN trimmed
        raise AssertionError(
            f"agent-doctor.ps1:{i} uses Select-Object -First 1 in an "
            f"unclassified pipeline -- review and classify: {stripped}"
        )
