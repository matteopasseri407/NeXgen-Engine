"""Unit test per nexgen_core.runtimes: adattatori CLI (postura + guardrail hook).

Ogni test punta `home`/`vault` su tmp_path -- MAI sulla home reale. La
macchina di sviluppo ha claude/codex/opencode/agy realmente sul PATH, quindi
ogni test che riguarda is_installed() deve azzerare `shutil.which` in modo
esplicito: altrimenti il risultato dipenderebbe da cosa capita di essere
installato su chi esegue i test, non dalla logica sotto test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.guard import GuardRunner
from nexgen_core.runtimes import REGISTRY, apply_all
from nexgen_core.runtimes.antigravity import AntigravityRuntime
from nexgen_core.runtimes.base import GuardrailError
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.runtimes.codex import CodexRuntime
from nexgen_core.runtimes.opencode import OpenCodeRuntime


@pytest.fixture(autouse=True)
def _no_ambient_binaries(monkeypatch):
    """Azzera `shutil.which` in ogni modulo adattatore: nessun test deve
    dipendere da cosa e' realmente installato sulla macchina che li esegue."""
    for mod in ("claude", "codex", "opencode", "antigravity"):
        monkeypatch.setattr(f"nexgen_core.runtimes.{mod}.shutil.which", lambda name: None)


# ── Claude ───────────────────────────────────────────────────────────────

def test_claude_apply_posture_merges_and_preserves_foreign_keys(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = {
        "statusLine": {"type": "command", "command": "echo hi"},
        "permissions": {"allow": ["Bash(ls:*)"]},
    }
    settings.write_text(json.dumps(original, indent=2), encoding="utf-8")

    rt = ClaudeRuntime()
    assert rt.is_installed(home) is True  # settings.json e' l'impronta propria di Claude

    action = rt.apply_posture(home, "bypass")
    assert action is not None

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"]["defaultMode"] == "bypassPermissions"
    assert data["permissions"]["allow"] == ["Bash(ls:*)"]  # chiave dell'utente sopravvive
    assert data["statusLine"] == original["statusLine"]  # chiave estranea sopravvive
    assert data["skipDangerousModePermissionPrompt"] is True


def test_claude_apply_posture_backs_up_before_writing(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original_text = json.dumps({"permissions": {"defaultMode": "default"}})
    settings.write_text(original_text, encoding="utf-8")

    ClaudeRuntime().apply_posture(home, "bypass")

    backups = list(home.joinpath(".claude").glob("settings.json.pre-permissions-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original_text


def test_claude_apply_posture_is_idempotent(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {}}), encoding="utf-8")

    rt = ClaudeRuntime()
    first = rt.apply_posture(home, "bypass")
    assert first is not None
    after_first = settings.read_text(encoding="utf-8")

    second = rt.apply_posture(home, "bypass")
    assert second is None  # nulla da fare la seconda volta
    assert settings.read_text(encoding="utf-8") == after_first
    backups = list(home.joinpath(".claude").glob("settings.json.pre-permissions-*.bak"))
    assert len(backups) == 1  # nessun secondo backup per una scrittura che non e' avvenuta


def test_claude_install_guardrail_registers_hook_and_preserves_other_events(tmp_path: Path):
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "node other.mjs"}]}]},
    }), encoding="utf-8")

    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy body\n", encoding="utf-8")

    rt = ClaudeRuntime()
    action = rt.install_guardrail(home, hook_source, tmp_path / "unused-engine-hooks")
    assert action is not None
    assert (claude_dir / "guardrail-catastrophic.mjs").read_text(encoding="utf-8") == "// policy body\n"

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["hooks"]["SessionStart"]  # evento estraneo sopravvive intatto
    pretool = data["hooks"]["PreToolUse"]
    assert pretool[0]["matcher"] == "Bash"
    assert "guardrail-catastrophic.mjs" in pretool[0]["hooks"][0]["command"]

    # Idempotenza: una seconda chiamata non aggiunge una seconda voce.
    again = rt.install_guardrail(home, hook_source, tmp_path / "unused-engine-hooks")
    assert again is None
    data2 = json.loads(settings.read_text(encoding="utf-8"))
    assert len(data2["hooks"]["PreToolUse"]) == 1


def test_claude_malformed_settings_refuses_without_writing(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"hooks": "not-an-object"})
    settings.write_text(original, encoding="utf-8")

    rt = ClaudeRuntime()
    with pytest.raises(GuardrailError):
        rt.install_guardrail(home, tmp_path / "irrelevant.mjs", tmp_path)
    # Nessuna scrittura ne' backup per una config che non si puo' modificare in sicurezza.
    assert settings.read_text(encoding="utf-8") == original
    assert not list(home.joinpath(".claude").glob("*.bak"))


def test_claude_is_installed_regression_directory_footprint(tmp_path: Path):
    """~/.claude/skills e' creato dal materializzatore di skill di QUESTO
    layer, indipendentemente da Claude essere installato. Solo settings.json
    (scritto solo da Claude stesso) o il binario contano."""
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    assert ClaudeRuntime().is_installed(home) is False


# ── Codex ────────────────────────────────────────────────────────────────

CODEX_TOML = '''# commento dell'utente
model = "o4"

[profile.dev]
foo = "bar"
'''


def test_codex_apply_posture_surgical_edit_preserves_sections_and_comments(tmp_path: Path):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(CODEX_TOML, encoding="utf-8")

    rt = CodexRuntime()
    action = rt.apply_posture(home, "bypass")
    assert action is not None

    text = config.read_text(encoding="utf-8")
    assert "# commento dell'utente" in text
    assert 'model = "o4"' in text
    assert "[profile.dev]" in text
    assert 'foo = "bar"' in text
    assert 'approval_policy = "never"' in text
    assert 'sandbox_mode = "danger-full-access"' in text

    backups = list(home.joinpath(".codex").glob("config.toml.pre-permissions-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == CODEX_TOML


def test_codex_apply_posture_idempotent(tmp_path: Path):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(CODEX_TOML, encoding="utf-8")

    rt = CodexRuntime()
    rt.apply_posture(home, "bypass")
    after_first = config.read_text(encoding="utf-8")
    assert rt.apply_posture(home, "bypass") is None
    assert config.read_text(encoding="utf-8") == after_first


def test_codex_unsupported_posture_skipped_silently(tmp_path: Path):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(CODEX_TOML, encoding="utf-8")
    assert CodexRuntime().apply_posture(home, "ask") is None
    assert config.read_text(encoding="utf-8") == CODEX_TOML


def test_codex_no_guardrail_ever(tmp_path: Path):
    home = tmp_path / "home"
    assert CodexRuntime().install_guardrail(home, tmp_path / "x.mjs", tmp_path) is None


def test_codex_is_installed_regression_config_written_by_own_renderer(tmp_path: Path):
    """config.toml e' scritto anche dal renderer MCP di questo stesso layer
    ad ogni ciclo, installato Codex o no: la sua sola esistenza non basta."""
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("model = \"o4\"\n", encoding="utf-8")
    assert CodexRuntime().is_installed(home) is False


# ── OpenCode ─────────────────────────────────────────────────────────────

def _write_opencode_config(home: Path) -> Path:
    path = home / ".config" / "opencode" / "opencode.jsonc"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "permission": {"webfetch": "ask"},
        "plugin": ["some-other-plugin"],
    }, indent=2), encoding="utf-8")
    return path


def test_opencode_apply_posture_merges_and_preserves_other_permission_keys(tmp_path: Path):
    home = tmp_path / "home"
    config = _write_opencode_config(home)

    action = OpenCodeRuntime().apply_posture(home, "bypass")
    assert action is not None

    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["permission"]["edit"] == "allow"
    assert data["permission"]["bash"] == "allow"
    assert data["permission"]["webfetch"] == "ask"  # dimensione estranea sopravvive
    assert data["plugin"] == ["some-other-plugin"]  # plugin dell'utente intatto


def test_opencode_install_guardrail_registers_plugin_and_preserves_others(tmp_path: Path):
    home = tmp_path / "home"
    config = _write_opencode_config(home)
    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")
    engine_hooks_dir = tmp_path / "engine-hooks"
    engine_hooks_dir.mkdir()
    (engine_hooks_dir / "opencode-guardrail-plugin.mjs").write_text("// adapter\n", encoding="utf-8")

    rt = OpenCodeRuntime()
    action = rt.install_guardrail(home, hook_source, engine_hooks_dir)
    assert action is not None

    data = json.loads(config.read_text(encoding="utf-8"))
    assert "some-other-plugin" in data["plugin"]
    assert any("opencode-guardrail-plugin.mjs" in p for p in data["plugin"] if isinstance(p, str))

    plugin_dir = config.parent
    assert (plugin_dir / "opencode-guardrail-plugin.mjs").read_text(encoding="utf-8") == "// adapter\n"
    body_dst = plugin_dir / "nexgen-guardrail-hooks" / "guardrail-catastrophic.mjs"
    assert body_dst.read_text(encoding="utf-8") == "// policy\n"
    sidecar = json.loads((plugin_dir / "nexgen-guardrail.config.json").read_text(encoding="utf-8"))
    assert sidecar["hooks"][0]["file"] == str(body_dst)

    # Idempotenza
    again = rt.install_guardrail(home, hook_source, engine_hooks_dir)
    assert again is None


def test_opencode_is_installed_regression_config_written_by_own_renderer(tmp_path: Path):
    home = tmp_path / "home"
    _write_opencode_config(home)
    assert OpenCodeRuntime().is_installed(home) is False


def test_opencode_is_installed_via_own_bin_marker(tmp_path: Path):
    home = tmp_path / "home"
    bin_dir = home / ".opencode" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "opencode").write_text("#!/bin/sh\n", encoding="utf-8")
    assert OpenCodeRuntime().is_installed(home) is True


# ── Antigravity ──────────────────────────────────────────────────────────

def _write_antigravity_settings(home: Path) -> Path:
    path = home / ".gemini" / "antigravity-cli" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"someOtherKey": "keep-me"}), encoding="utf-8")
    return path


def test_antigravity_apply_posture_writes_both_keys_and_preserves_others(tmp_path: Path):
    home = tmp_path / "home"
    settings = _write_antigravity_settings(home)
    action = AntigravityRuntime().apply_posture(home, "bypass")
    assert action is not None
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["toolPermission"] == "always-proceed"
    assert data["artifactReviewPolicy"] == "always-proceed"
    assert data["someOtherKey"] == "keep-me"


def test_antigravity_install_guardrail_preserves_foreign_hooks_json_keys(tmp_path: Path):
    home = tmp_path / "home"
    _write_antigravity_settings(home)
    hooks_path = home / ".gemini" / "config" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(json.dumps({"some-other-tool-hook": {"enabled": True}}), encoding="utf-8")

    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")
    engine_hooks_dir = tmp_path / "engine-hooks"
    engine_hooks_dir.mkdir()
    (engine_hooks_dir / "antigravity-guardrail-adapter.mjs").write_text("// adapter\n", encoding="utf-8")

    rt = AntigravityRuntime()
    action = rt.install_guardrail(home, hook_source, engine_hooks_dir)
    assert action is not None

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["some-other-tool-hook"] == {"enabled": True}  # chiave estranea sopravvive
    assert data["nexgen-guardrail"]["PreToolUse"][0]["matcher"] == "run_command"

    backups = list(hooks_path.parent.glob("hooks.json.pre-permissions-*.bak"))
    assert len(backups) == 1

    again = rt.install_guardrail(home, hook_source, engine_hooks_dir)
    assert again is None


def test_antigravity_is_installed_regression_directory_footprint(tmp_path: Path):
    """~/.gemini/antigravity-cli/skills e' creato dal materializzatore di
    skill di questo stesso layer -- settings.json (scritto solo da
    Antigravity) e' l'unico segnale valido oltre al binario."""
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity-cli" / "skills").mkdir(parents=True)
    assert AntigravityRuntime().is_installed(home) is False


# ── apply_all: l'integrazione ────────────────────────────────────────────

def test_apply_all_skips_every_cli_when_none_installed(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    actions = apply_all(
        home=home,
        engine_hooks_dir=tmp_path / "engine-hooks",
        posture={"claude": "bypass", "codex": "bypass", "opencode": "bypass", "antigravity": "bypass"},
        guardrail_source=tmp_path / "does-not-matter.mjs",
    )
    assert actions == []
    assert not any(home.iterdir())  # nessun file creato per CLI assenti


def test_apply_all_blocks_posture_when_guardrail_install_fails(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"hooks": "not-an-object"})
    settings.write_text(original, encoding="utf-8")

    hook_source = tmp_path / "guardrail-catastrophic.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")

    actions = apply_all(
        home=home,
        engine_hooks_dir=tmp_path / "engine-hooks",
        posture={"claude": "bypass"},
        guardrail_source=hook_source,
    )
    assert any("[WARN]" in a and "claude" in a for a in actions)
    # La postura non deve MAI raggiungere il disco senza il suo guardrail.
    assert settings.read_text(encoding="utf-8") == original


def test_registry_has_one_adapter_per_supported_cli():
    assert set(REGISTRY) == {"claude", "codex", "opencode", "antigravity"}


# ── GuardRunner.apply_runtime_permissions: il punto di integrazione ───────

def test_guard_apply_runtime_permissions_noop_without_manifest(tmp_path: Path):
    runner = GuardRunner(vault_data=tmp_path / "vault", home=tmp_path / "home")
    assert runner.apply_runtime_permissions() == []


def test_guard_apply_runtime_permissions_end_to_end(tmp_path: Path):
    vault = tmp_path / "vault"
    permissions_dir = vault / "03-INFRA" / "agent-universal-layer" / "permissions"
    (permissions_dir / "hooks").mkdir(parents=True)
    (permissions_dir / "hooks" / "guardrail-catastrophic.mjs").write_text("// policy\n", encoding="utf-8")
    (permissions_dir / "manifest.yaml").write_text(
        "posture:\n  claude: bypass\nhooks:\n  - name: guardrail-catastrophic\n"
        "    file: hooks/guardrail-catastrophic.mjs\n    targets: [claude]\n",
        encoding="utf-8",
    )

    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {}}), encoding="utf-8")

    engine_root = tmp_path / "engine" / "03-INFRA"
    (engine_root / "agent-universal-layer" / "hooks").mkdir(parents=True)

    runner = GuardRunner(vault_data=vault, engine_root=engine_root, home=home)
    actions = runner.apply_runtime_permissions()
    assert actions  # qualcosa e' successo

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"]["defaultMode"] == "bypassPermissions"
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


def test_guard_apply_runtime_permissions_rejects_path_traversal(tmp_path: Path):
    vault = tmp_path / "vault"
    permissions_dir = vault / "03-INFRA" / "agent-universal-layer" / "permissions"
    permissions_dir.mkdir(parents=True)
    outside = tmp_path / "outside.mjs"
    outside.write_text("// not part of the vault\n", encoding="utf-8")
    (permissions_dir / "manifest.yaml").write_text(
        f"posture:\n  claude: bypass\nhooks:\n  - name: evil\n    file: {outside}\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"permissions": {}})
    settings.write_text(original, encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    actions = runner.apply_runtime_permissions()
    assert any("WARN" in a for a in actions)
    assert settings.read_text(encoding="utf-8") == original


# ── Event Sink Tests ───────────────────────────────────────────────────

def test_claude_install_event_sink_registers_stop_and_pretooluse(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")

    sink_source = tmp_path / "nexgen-event-sink.mjs"
    sink_source.write_text("// dummy sink", encoding="utf-8")

    rt = ClaudeRuntime()
    action = rt.install_event_sink(home, sink_source)
    assert action is not None
    assert "event sink registered" in action

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert "Stop" in data["hooks"]
    assert "PreToolUse" in data["hooks"]
    assert (home / ".claude" / "nexgen-event-sink.mjs").is_file()

    # Idempotent
    action2 = rt.install_event_sink(home, sink_source)
    assert action2 is None


def test_antigravity_install_event_sink_registers_hooks_json(tmp_path: Path):
    home = tmp_path / "home"
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")

    sink_source = tmp_path / "nexgen-event-sink.mjs"
    sink_source.write_text("// dummy sink", encoding="utf-8")

    rt = AntigravityRuntime()
    action = rt.install_event_sink(home, sink_source)
    assert action is not None

    hooks_path = home / ".gemini" / "config" / "hooks.json"
    assert hooks_path.is_file()
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert "nexgen-event-sink" in data
    assert "Stop" in data["nexgen-event-sink"]
    assert "PreToolUse" in data["nexgen-event-sink"]


def test_codex_install_event_sink_registers_hooks_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nexgen_core.runtimes.codex.shutil.which", lambda name: "/usr/bin/codex" if name == "codex" else None)
    home = tmp_path / "home"

    sink_source = tmp_path / "nexgen-event-sink.mjs"
    sink_source.write_text("// dummy sink", encoding="utf-8")

    rt = CodexRuntime()
    action = rt.install_event_sink(home, sink_source)
    assert action is not None

    hooks_path = home / ".codex" / "hooks.json"
    assert hooks_path.is_file()
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert "hooks" in data
    assert "Stop" in data["hooks"]
    assert "PreToolUse" in data["hooks"]
    assert "nexgen-event-sink" not in data


def test_codex_install_event_sink_purges_legacy_root_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nexgen_core.runtimes.codex.shutil.which", lambda name: "/usr/bin/codex" if name == "codex" else None)
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    hooks_path = codex_dir / "hooks.json"
    hooks_path.write_text(json.dumps({"nexgen-event-sink": {"enabled": True}}), encoding="utf-8")

    sink_source = tmp_path / "nexgen-event-sink.mjs"
    sink_source.write_text("// dummy sink", encoding="utf-8")

    rt = CodexRuntime()
    action = rt.install_event_sink(home, sink_source)
    assert action is not None

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert "nexgen-event-sink" not in data
    assert "hooks" in data



def test_opencode_install_event_sink_registers_plugin(tmp_path: Path):
    home = tmp_path / "home"
    config = home / ".config" / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")

    sink_source = tmp_path / "nexgen-event-sink.mjs"
    sink_source.write_text("// dummy sink", encoding="utf-8")

    rt = OpenCodeRuntime()
    action = rt.install_event_sink(home, sink_source)
    assert action is not None
    assert (home / ".config" / "opencode" / "nexgen-event-sink.mjs").is_file()


def test_nexgen_event_sink_script_e2e_socket_and_failsafe(tmp_path: Path):
    """Il sink emette solo per le sessioni che il cockpit ha marcato vocali.

    Il gate su COCKPIT_VOCALE e' voluto: senza, ogni terminale aperto si
    metterebbe a parlare. Un test che non lo imposta e poi attende su accept()
    non fallisce, si appende - e con lui l'intera suite, nascondendo anche
    tutti i test dopo di lui. Qui ogni attesa ha una scadenza.
    """
    import os
    import socket
    import subprocess
    import time

    script_path = Path(__file__).resolve().parents[1] / "hooks" / "nexgen-event-sink.mjs"
    if not script_path.is_file():
        pytest.skip("nexgen-event-sink.mjs not in hooks folder")

    vocal = os.environ.copy()
    vocal["COCKPIT_VOCALE"] = "1"
    vocal["COCKPIT_SESSION_ID"] = "voice-testrun"

    # 1. Socket assente -> esce 0 subito, e non sporca mai stdout.
    #    Il margine e' largo perche' il runner Windows avvia node a freddo.
    sock_missing = tmp_path / "missing.sock"
    vocal["NEXGEN_EVENT_IPC_PATH"] = str(sock_missing)
    t0 = time.time()
    res = subprocess.run(
        ["node", str(script_path), "on_done", "claude", "test hello"],
        env=vocal, capture_output=True, text=True, timeout=20,
    )
    assert res.returncode == 0
    assert time.time() - t0 < 15.0
    assert res.stdout == ""  # never pollutes stdout

    if sys.platform == "win32" or not hasattr(socket, "AF_UNIX"):
        return

    # 2. Socket vivo -> il payload arriva (solo POSIX).
    sock_live = tmp_path / "live.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_live))
    srv.listen(1)
    srv.settimeout(15)
    vocal["NEXGEN_EVENT_IPC_PATH"] = str(sock_live)

    proc = subprocess.Popen(
        ["node", str(script_path), "on_step", "codex", "analyzing code"],
        env=vocal, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        proc.kill()
        srv.close()
        pytest.fail("il sink non ha emesso niente per una sessione vocale")
    data = conn.recv(4096).decode("utf-8")
    conn.close()
    srv.close()
    proc.wait(timeout=10)
    assert proc.returncode == 0
    assert '"event":"on_step"' in data
    assert '"cli":"codex"' in data
    assert '"text":"analyzing code"' in data

    # 3. Sessione non marcata vocale -> silenzio. E' la garanzia per cui il
    #    gate esiste, e prima non era coperta da nessun test.
    sock_quiet = tmp_path / "quiet.sock"
    srv2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv2.bind(str(sock_quiet))
    srv2.listen(1)
    srv2.settimeout(3)
    plain = os.environ.copy()
    plain.pop("COCKPIT_VOCALE", None)
    plain["NEXGEN_EVENT_IPC_PATH"] = str(sock_quiet)
    res = subprocess.run(
        ["node", str(script_path), "on_step", "codex", "non deve uscire"],
        env=plain, capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 0
    with pytest.raises(socket.timeout):
        srv2.accept()
    srv2.close()

    # 4. Antigravity Stop hook with transcriptPath (JSONL format)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"step_index":0,"source":"USER_EXPLICIT","type":"USER_INPUT","content":"ciao"}\n'
        '{"step_index":1,"source":"MODEL","type":"PLANNER_RESPONSE","content":"Risposta vocale di prova da Antigravity."}\n',
        encoding="utf-8",
    )
    srv3 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_live3 = tmp_path / "live3.sock"
    srv3.bind(str(sock_live3))
    srv3.listen(1)
    srv3.settimeout(20)
    env3 = os.environ.copy()
    env3["COCKPIT_VOCALE"] = "1"
    env3["NEXGEN_EVENT_IPC_PATH"] = str(sock_live3)

    agy_payload = json.dumps({
        "conversationId": "test-agy-conv-123",
        "transcriptPath": str(transcript),
        "terminationReason": "model_stop",
    })
    proc3 = subprocess.Popen(
        ["node", str(script_path), "on_done", "antigravity"],
        env=env3,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc3.stdin.write(agy_payload)
    proc3.stdin.close()

    conn3, _ = srv3.accept()
    data3 = conn3.recv(4096).decode("utf-8")
    conn3.close()
    srv3.close()
    proc3.wait(timeout=10)
    assert proc3.returncode == 0
    assert '"event":"on_done"' in data3
    assert '"cli":"antigravity"' in data3
    assert '"text":"Risposta vocale di prova da Antigravity."' in data3
    assert '"session_id":"test-agy-conv-123"' in data3
