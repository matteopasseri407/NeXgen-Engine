"""Unit test per Fase 5: depwatch.py, il tetto dell'updater non presidiato,
il battito che collega davvero le sue promesse, e retired_servers."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import depwatch
from nexgen_core.beat import Heartbeat
from nexgen_core.config import load_mcp_manifest
from nexgen_core.depwatch import run_depwatch
from nexgen_core.renderer import McpRenderer

PINNED_COMMIT = "a" * 40
UPSTREAM_COMMIT = "b" * 40


def _write_pinned_manifests(vault: Path) -> None:
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    mcp_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    (mcp_dir / "manifest.yaml").write_text(
        """
schema_version: 1
servers:
  fake-npx-server:
    transport: stdio
    command: npx
    args: ["-y", "fake-npm-pkg@2.0.0"]
    targets: [claude]
""",
        encoding="utf-8",
    )
    (skills_dir / "skills.manifest.yaml").write_text(
        f"""
schema_version: 1
skills:
  fake-github-skill:
    origin: github
    repo: "https://example.invalid/fake/repo.git"
    commit: "{PINNED_COMMIT}"
    targets: [claude]
  fake-installer-skill:
    origin: installer
    version: "1.0.0"
    install: ["npx", "--yes", "fake-installer-pkg@1.0.0", "install"]
    targets: [claude]
""",
        encoding="utf-8",
    )


def _fake_git_stale(_repo: str) -> str | None:
    return UPSTREAM_COMMIT


def _fake_npm_resolver(package: str) -> str | None:
    return {"fake-installer-pkg": "1.2.0", "fake-npm-pkg": "2.0.0"}.get(package)


def test_depwatch_writes_a_report_when_something_moved_upstream(tmp_path: Path):
    vault = tmp_path / "vault"
    state_dir = tmp_path / "state"
    _write_pinned_manifests(vault)

    result = run_depwatch(
        vault_data=vault,
        state_dir=state_dir,
        git_ls_remote=_fake_git_stale,
        npm_latest_version=_fake_npm_resolver,
    )

    assert result.wrote is True
    assert result.report_path == state_dir / "third-party-upgrades.md"
    assert result.report_path.is_file()
    content = result.report_path.read_text(encoding="utf-8")

    # github skill and installer skill moved; the npx MCP server is current.
    assert "fake-github-skill" in content
    assert UPSTREAM_COMMIT in content
    assert "fake-installer-pkg" in content
    assert "fake-npm-pkg" in content
    stale = {f.what for f in result.findings if f.stale}
    assert any("fake-github-skill" in w for w in stale)
    assert any("fake-installer-pkg" in w for w in stale)
    up_to_date = {f.what for f in result.findings if not f.stale}
    assert any("fake-npm-pkg" in w for w in up_to_date)


def test_depwatch_offline_writes_nothing_and_reports_nothing(tmp_path: Path):
    """Being offline is not an incident: every upstream check fails to
    resolve, so Dependency Watch must not write the report file at all."""
    vault = tmp_path / "vault"
    state_dir = tmp_path / "state"
    _write_pinned_manifests(vault)

    result = run_depwatch(
        vault_data=vault,
        state_dir=state_dir,
        git_ls_remote=lambda _repo: None,
        npm_latest_version=lambda _pkg: None,
    )

    assert result.wrote is False
    assert result.report_path is None
    assert not (state_dir / "third-party-upgrades.md").exists()
    assert not state_dir.exists()


def test_depwatch_with_nothing_pinned_writes_nothing(tmp_path: Path):
    vault = tmp_path / "vault"
    state_dir = tmp_path / "state"
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "skills").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").write_text(
        "schema_version: 1\nservers: {}\n", encoding="utf-8"
    )
    (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text(
        "schema_version: 1\nskills: {}\n", encoding="utf-8"
    )

    result = run_depwatch(vault_data=vault, state_dir=state_dir)

    assert result.wrote is False
    assert result.report_path is None


def test_depwatch_never_calls_the_megaphone(tmp_path: Path, monkeypatch):
    """Dependency Watch never notifies: the module must not even reference
    the Megaphone, and a full run (with stale pins) must not trigger it."""
    source = inspect.getsource(depwatch)
    assert "Megaphone" not in source
    assert "megaphone" not in source.lower()

    from nexgen_core.megaphone import Megaphone

    def _forbidden(self, *_a, **_kw):
        raise AssertionError("Dependency Watch must never call the Megaphone")

    monkeypatch.setattr(Megaphone, "send_alert", _forbidden)

    vault = tmp_path / "vault"
    state_dir = tmp_path / "state"
    _write_pinned_manifests(vault)

    result = run_depwatch(
        vault_data=vault,
        state_dir=state_dir,
        git_ls_remote=_fake_git_stale,
        npm_latest_version=_fake_npm_resolver,
    )
    assert result.wrote is True  # completed normally, never touched the alert path


# --------------------------------------------------------------------------
# The unattended updater's ceiling.
# --------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "nexgen depwatch test",
        "GIT_AUTHOR_EMAIL": "nexgen-depwatch-test@localhost",
        "GIT_COMMITTER_NAME": "nexgen depwatch test",
        "GIT_COMMITTER_EMAIL": "nexgen-depwatch-test@localhost",
    }
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env)


def _write_release(repo: Path, version: str, previous: str | None = None) -> None:
    (repo / "03-INFRA").mkdir(exist_ok=True)
    (repo / "03-INFRA" / ".gitkeep").touch()
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    changelog = f"## [{version}] - 2026-08-01\n\n### Changed\n\n- Release {version}.\n"
    if previous:
        changelog += f"\n## [{previous}] - 2026-07-31\n\n### Added\n\n- Release {previous}.\n"
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _git(repo, "add", "VERSION", "CHANGELOG.md", "03-INFRA/.gitkeep")
    _git(repo, "commit", "-m", f"release v{version}")
    _git(repo, "tag", f"v{version}")


def _upgrade_fixture(tmp_path: Path, first: str, second: str) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _write_release(origin, first)
    engine = tmp_path / "engine"
    _git(tmp_path, "clone", str(origin), str(engine))
    _write_release(origin, second, previous=first)
    return origin, engine


def _env(engine: Path) -> dict[str, str]:
    return {**os.environ, "AGENT_ENGINE_ROOT": str(engine / "03-INFRA"), "AGENT_VAULT_DATA": str(engine)}


def _load_updater():
    import importlib.util

    script = SCRIPTS_DIR / "nexgen_core" / "updater.py"
    spec = importlib.util.spec_from_file_location("nexgen_update_phase5_under_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_unattended_refuses_a_minor_jump(tmp_path: Path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path, "0.1.0", "0.2.0")
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    def must_not_prompt(_prompt: str) -> str:
        raise AssertionError("unattended mode must never ask for confirmation")

    result = updater.main(
        ["--unattended"], environ=_env(engine), input_fn=must_not_prompt, which=lambda _n: None
    )

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    error = capsys.readouterr().err
    assert "minor or major" in error
    # The failure must name the recovery (the interactive command), not the check.
    assert "nexgen-update --target" in error


def test_unattended_applies_a_patch_jump_without_prompting(tmp_path: Path):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path, "0.1.0", "0.1.1")

    def must_not_prompt(_prompt: str) -> str:
        raise AssertionError("unattended mode must never ask for confirmation")

    result = updater.main(
        ["--unattended"], environ=_env(engine), input_fn=must_not_prompt, which=lambda _n: None
    )

    assert result == 0
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.1"


def test_interactive_mode_still_has_no_ceiling(tmp_path: Path):
    """The interactive path is contract for people already using it: a minor
    jump must still go through with --yes, exactly as before this change."""
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path, "0.1.0", "0.2.0")

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _n: None)

    assert result == 0
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"


# --------------------------------------------------------------------------
# The beat actually wiring its own promises.
# --------------------------------------------------------------------------

def test_beat_runs_dependency_watch_and_self_upgrade(tmp_path: Path):
    """run_beat() must exercise both maintenance duties its own docstring
    promises, without letting either one crash the liveness answer."""
    vault = tmp_path / "vault"
    state_dir = tmp_path / "state"
    engine_root = tmp_path / "engine-root"
    engine_root.mkdir(parents=True)
    _write_pinned_manifests(vault)

    beat = Heartbeat(state_dir=state_dir, vault_data=vault, engine_root=engine_root)
    result = beat.run_beat()

    assert "liveness_ok" in result and "liveness_msg" in result
    assert "dependency_watch" in result
    assert "self_upgrade" in result
    # engine_root is not a git clone at all here: the self-upgrader must fail
    # closed and be reported, never raise out of run_beat().
    assert result["self_upgrade"]["ok"] is False


def test_beat_liveness_file_is_not_shared_with_the_megaphone_debounce(tmp_path: Path):
    state_dir = tmp_path / "state"
    beat = Heartbeat(state_dir=state_dir, vault_data=tmp_path / "vault", engine_root=tmp_path / "engine")
    beat.record_liveness()

    # The rule is that this file is not the Megaphone's debounce file. Sharing
    # one is what froze liveness behind the debounce: two subsystems writing
    # the same path under different update rules.
    assert beat.liveness_file != beat.megaphone.state_file

    # And the first line stays a bare timestamp, so any reader that only wants
    # to know when the guard last finished keeps working -- including the
    # previous release, which reads this file and knows nothing about what may
    # follow. What comes after is written by the same writer at the same
    # moment, so it cannot drift out of step with the timestamp above it.
    lines = beat.liveness_file.read_text(encoding="utf-8").splitlines()
    float(lines[0])


# --------------------------------------------------------------------------
# retired_servers: declared, and now actually read and enforced.
# --------------------------------------------------------------------------

def _write_manifest_with_retirement(vault: Path) -> None:
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "manifest.yaml").write_text(
        """
schema_version: 1
retired_servers: [old-connector]
servers:
  old-connector:
    transport: stdio
    command: fake-old-cmd
    targets: [claude, antigravity]
  keeper:
    transport: stdio
    command: fake-keep-cmd
    targets: [claude, antigravity]
""",
        encoding="utf-8",
    )


def test_load_mcp_manifest_reads_retired_servers_and_skips_the_conflicting_active_entry(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_manifest_with_retirement(vault)
    manifest_path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"

    data = load_mcp_manifest(manifest_path)

    assert data["retired_servers"] == {"old-connector"}
    # Declared both active and retired: the manifest is inconsistent, so the
    # active entry is skipped with a warning -- the document is not rejected.
    assert "old-connector" not in data["servers"]
    assert "keeper" in data["servers"]


def test_renderer_removes_a_retired_server_from_every_generated_cli_config(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_manifest_with_retirement(vault)
    home = tmp_path / "home"
    home.mkdir()

    # Simulate a machine where the connector was rendered before it was retired.
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"old-connector": {"command": "fake-old-cmd"}, "custom-live": {"command": "x"}}}),
        encoding="utf-8",
    )
    (home / ".gemini" / "antigravity-ide").mkdir(parents=True)
    (home / ".gemini" / "antigravity-ide" / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"old-connector": {"command": "fake-old-cmd"}}}), encoding="utf-8"
    )

    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    renderer.render_claude(write=True)
    renderer.render_antigravity(write=True)

    claude_data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert "old-connector" not in claude_data["mcpServers"]
    assert "keeper" in claude_data["mcpServers"]
    assert "custom-live" in claude_data["mcpServers"]  # untouched, additive preservation still applies

    agy_data = json.loads((home / ".gemini" / "antigravity-ide" / "mcp_config.json").read_text(encoding="utf-8"))
    assert "old-connector" not in agy_data["mcpServers"]
    assert "keeper" in agy_data["mcpServers"]
