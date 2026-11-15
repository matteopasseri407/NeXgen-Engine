"""The desktop apps are first-class: they are detected, their shared config is checked, and fan-outs stay coherent.

ChatGPT desktop bundles Codex and shares ~/.codex with the CLI; Antigravity
IDE shares ~/.gemini with `agy`. A machine that has only the app must still
be configured, and a version skew or a stray copy must be visible.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import bootstrap, platforms  # noqa: E402
from nexgen_core.checks import desktop_checks, mcp_checks  # noqa: E402
from nexgen_core.paths import canonical_instructions  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

def _symlinks_supported() -> bool:
    """Whether this machine may create a symlink at all: Windows denies it without a privilege."""
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "target"
        target.write_text("x", encoding="utf-8")
        try:
            (Path(folder) / "link").symlink_to(target)
        except OSError:
            return False
        return True


requires_symlinks = pytest.mark.skipif(not _symlinks_supported(), reason="this machine may not create symlinks")


@pytest.fixture(autouse=True)
def _own_codex_home(monkeypatch):
    """Codex's config resolves $CODEX_HOME first; a developer's shell must not leak into these tests."""
    monkeypatch.delenv("CODEX_HOME", raising=False)


def _canon(vault: Path, content: str = "# Agents Rules\n") -> Path:
    canon = canonical_instructions(vault)
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text(content, encoding="utf-8")
    return canon


# --------------------------------------------------------------- 1. detection


def test_the_desktop_apps_declare_the_targets_they_share():
    targets = {target for app in platforms.DESKTOP_APPS for target in app.targets}
    assert targets == {"codex", "antigravity"}


def test_chatgpt_desktop_on_linux_is_found_at_its_install_root():
    apps = platforms.installed_desktop_apps(
        platform="linux", exists=lambda p: p == Path("/usr/lib/chatgpt"),
    )
    assert [a.name for a in apps] == ["ChatGPT"]


def test_antigravity_desktop_on_linux_is_found_under_either_location():
    apps = platforms.installed_desktop_apps(
        platform="linux", exists=lambda p: p == Path("/usr/share/antigravity"),
    )
    assert [a.name for a in apps] == ["Antigravity"]


def test_desktop_apps_on_macos_and_windows_use_their_own_locations():
    mac = platforms.installed_desktop_apps(
        platform="darwin", exists=lambda p: p == Path("/Applications/ChatGPT.app"),
    )
    assert [a.name for a in mac] == ["ChatGPT"]
    win_root = Path("C:/Users/user/AppData/Local")
    win = platforms.installed_desktop_apps(
        platform="win32",
        env={"LOCALAPPDATA": str(win_root)},
        exists=lambda p: p == win_root / "Programs" / "Antigravity",
    )
    assert [a.name for a in win] == ["Antigravity"]


def test_nothing_installed_means_no_desktop_app():
    assert platforms.installed_desktop_apps(platform="linux", exists=lambda p: False) == []


def test_the_store_package_family_of_the_codex_app_is_declared_for_windows():
    """A Store Codex app has no `Programs\\ChatGPT` folder; its package family is the evidence."""
    declared = {app.name: dict(app.package_families) for app in platforms.DESKTOP_APPS}
    assert declared == {"ChatGPT": {"win32": ("OpenAI.Codex_2p2nqsd0c76g0",)}, "Antigravity": {}}


def test_a_store_package_counts_as_an_installed_app():
    win_root = Path("C:/Users/user/AppData/Local")
    apps = platforms.installed_desktop_apps(
        platform="win32",
        env={"LOCALAPPDATA": str(win_root)},
        exists=lambda p: p == win_root / "Packages" / "OpenAI.Codex_2p2nqsd0c76g0",
    )
    assert [a.name for a in apps] == ["ChatGPT"]


def test_another_apps_store_package_is_not_evidence_for_the_codex_app():
    win_root = Path("C:/Users/user/AppData/Local")
    apps = platforms.installed_desktop_apps(
        platform="win32",
        env={"LOCALAPPDATA": str(win_root)},
        exists=lambda p: p == win_root / "Packages" / "Claude_pzs8sxrjxfjjc",
    )
    assert apps == []


def test_a_store_package_folder_is_not_an_install_root():
    """Nothing inside `%LOCALAPPDATA%\\Packages` is the app's own layout, so it is never searched."""
    win_root = Path("C:/Users/user/AppData/Local")
    roots = platforms.existing_roots(
        platforms.DESKTOP_APPS[0], platform="win32", env={"LOCALAPPDATA": str(win_root)},
    )
    assert roots == []


def test_a_store_only_codex_app_still_gets_the_codex_target(tmp_path, monkeypatch):
    """The app-only machine this detection exists for: the Store package is the app, so ~/.codex is configured."""
    _no_cli(monkeypatch)
    win_root = Path("C:/Users/user/AppData/Local")
    store = win_root / "Packages" / "OpenAI.Codex_2p2nqsd0c76g0"
    monkeypatch.setattr(
        bootstrap,
        "installed_desktop_apps",
        lambda: platforms.installed_desktop_apps(
            platform="win32", env={"LOCALAPPDATA": str(win_root)}, exists=lambda p: p == store,
        ),
    )
    assert bootstrap.detect_clis(tmp_path) == ["codex"]


def _no_cli(monkeypatch):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None)


def test_desktop_only_chatgpt_still_gets_the_codex_target(tmp_path, monkeypatch):
    _no_cli(monkeypatch)
    monkeypatch.setattr(bootstrap, "installed_desktop_apps", lambda: [platforms.DESKTOP_APPS[0]])
    assert bootstrap.detect_clis(tmp_path) == ["codex"]


def test_desktop_only_antigravity_still_gets_the_antigravity_target(tmp_path, monkeypatch):
    _no_cli(monkeypatch)
    monkeypatch.setattr(bootstrap, "installed_desktop_apps", lambda: [platforms.DESKTOP_APPS[1]])
    assert bootstrap.detect_clis(tmp_path) == ["antigravity"]


def test_a_cli_and_its_desktop_app_are_listed_once(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/" + name if name == "codex" else None)
    monkeypatch.setattr(bootstrap, "installed_desktop_apps", lambda: [platforms.DESKTOP_APPS[0]])
    assert bootstrap.detect_clis(tmp_path) == ["codex"]


def test_no_cli_and_no_app_means_nothing_detected(tmp_path, monkeypatch):
    _no_cli(monkeypatch)
    monkeypatch.setattr(bootstrap, "installed_desktop_apps", lambda: [])
    assert bootstrap.detect_clis(tmp_path) == []


def test_desktop_only_machine_renders_both_targets(tmp_path):
    """Rendering never waited for a CLI binary; the desktop-only machine gets the same files."""
    from nexgen_core.renderer import McpRenderer

    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({"servers": {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"},
    }}))
    home = tmp_path / "home"
    home.mkdir()
    McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home).render_all(write=True)
    assert (home / ".codex" / "config.toml").is_file()
    assert (home / ".gemini" / "antigravity" / "mcp_config.json").is_file()


# ----------------------------------------------------- 2. Codex version skew


def test_skew_is_silent_without_the_desktop_app(monkeypatch):
    monkeypatch.setattr(desktop_checks, "installed_desktop_apps", lambda: [])
    assert desktop_checks.check_codex_desktop_skew() is None


def test_skew_is_silent_without_a_cli_codex(monkeypatch):
    monkeypatch.setattr(desktop_checks, "installed_desktop_apps", lambda: [platforms.DESKTOP_APPS[0]])
    monkeypatch.setattr(desktop_checks, "_bundled_codex", lambda: Path("/usr/lib/chatgpt/resources/codex"))
    monkeypatch.setattr(desktop_checks.shutil, "which", lambda name: None)
    assert desktop_checks.check_codex_desktop_skew() is None


def test_skew_stays_silent_when_the_app_is_a_store_package(monkeypatch):
    """The Store package has no install root, so no bundled Codex is compared: silence, not a guess."""
    win_root = Path("C:/Users/user/AppData/Local")
    monkeypatch.setattr(desktop_checks, "installed_desktop_apps", lambda: [platforms.DESKTOP_APPS[0]])
    monkeypatch.setattr(
        desktop_checks,
        "existing_roots",
        lambda app: platforms.existing_roots(app, platform="win32", env={"LOCALAPPDATA": str(win_root)}),
    )
    monkeypatch.setattr(desktop_checks.shutil, "which", lambda name: "/home/user/bin/codex")
    probes: list[str] = []
    monkeypatch.setattr(desktop_checks, "_binary_version", lambda binary: probes.append(str(binary)))
    assert desktop_checks.check_codex_desktop_skew() is None
    assert probes == []


def _skew_setup(monkeypatch, bundled_version, cli_version):
    monkeypatch.setattr(desktop_checks, "installed_desktop_apps", lambda: [platforms.DESKTOP_APPS[0]])
    monkeypatch.setattr(desktop_checks, "_bundled_codex", lambda: Path("/usr/lib/chatgpt/resources/codex"))
    monkeypatch.setattr(desktop_checks.shutil, "which", lambda name: "/home/user/.local/bin/codex")
    versions = {str(Path("/usr/lib/chatgpt/resources/codex")): bundled_version, "/home/user/.local/bin/codex": cli_version}
    monkeypatch.setattr(desktop_checks, "_binary_version", lambda binary: versions[str(binary)])


def test_skew_warns_and_names_both_versions(monkeypatch):
    _skew_setup(monkeypatch, "0.162.0-alpha.17.2", "0.159.2")
    outcome = desktop_checks.check_codex_desktop_skew()
    assert outcome.severity == Severity.WARN
    assert "0.162.0-alpha.17.2" in outcome.message and "0.159.2" in outcome.message
    assert outcome.remedy is None


def test_matching_versions_are_ok(monkeypatch):
    _skew_setup(monkeypatch, "0.159.2", "0.159.2")
    assert desktop_checks.check_codex_desktop_skew().severity == Severity.OK


def test_an_unreadable_version_is_undetermined_not_a_guess(monkeypatch):
    _skew_setup(monkeypatch, None, "0.159.2")
    outcome = desktop_checks.check_codex_desktop_skew()
    assert outcome.severity == Severity.UNDETERMINED


@pytest.mark.parametrize("output, expected", [
    ("codex-cli 0.162.0-alpha.17.2\n", "0.162.0-alpha.17.2"),
    ("codex-cli 0.159.2\n", "0.159.2"),
    ("something else entirely", None),
    ("", None),
])
def test_version_output_is_parsed(output, expected):
    assert desktop_checks.parse_codex_version(output) == expected


# ------------------------------------------- 5. Antigravity MCP fan-out check


def _mcp_setup(tmp_path):
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({"servers": {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"},
    }}))
    home = tmp_path / "home"
    gemini = home / ".gemini"
    # The officially read path (Google's MCP doc) holds the real file.
    canonical = gemini / "config" / "mcp_config.json"
    canonical.parent.mkdir(parents=True)
    body = {"mcpServers": {"alpha": {"command": "python", "args": ["-V"], "env": {}}}}
    canonical.write_text(json.dumps(body), encoding="utf-8")
    return vault, home, canonical, body


CONSUMER_DIRS = ("antigravity", "antigravity-cli", "antigravity-ide")


def _consumer(home: Path, name: str) -> Path:
    path = home / ".gemini" / name / "mcp_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@requires_symlinks
def test_fanout_is_ok_when_every_consumer_links_to_the_canonical(tmp_path):
    vault, home, canonical, _ = _mcp_setup(tmp_path)
    for name in CONSUMER_DIRS:
        _consumer(home, name).symlink_to(canonical)
    assert mcp_checks.check_antigravity_fanout(vault, home).severity == Severity.OK


def test_fanout_is_ok_for_an_identical_real_copy(tmp_path):
    vault, home, canonical, _ = _mcp_setup(tmp_path)
    for name in CONSUMER_DIRS:
        _consumer(home, name).write_bytes(canonical.read_bytes())
    assert mcp_checks.check_antigravity_fanout(vault, home).severity == Severity.OK


@requires_symlinks
def test_fanout_is_ok_on_a_machine_still_in_the_old_layout(tmp_path):
    """Older releases put the real file in `antigravity/` and linked `config/` to it.

    Nothing has drifted there, so the check must stay quiet until the next render
    migrates the layout: a BROKEN on every existing machine would be noise.
    """
    vault, home, canonical, body = _mcp_setup(tmp_path)
    legacy = _consumer(home, "antigravity")
    legacy.write_text(json.dumps(body), encoding="utf-8")
    canonical.unlink()
    canonical.symlink_to(legacy)
    for name in ("antigravity-cli", "antigravity-ide"):
        _consumer(home, name).symlink_to(legacy)
    assert mcp_checks.check_antigravity_fanout(vault, home).severity == Severity.OK


def test_fanout_is_broken_for_a_consumer_that_diverged_and_names_it(tmp_path):
    vault, home, canonical, _ = _mcp_setup(tmp_path)
    _consumer(home, "antigravity-cli").write_bytes(canonical.read_bytes())
    stale = _consumer(home, "antigravity-ide")
    stale.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    outcome = mcp_checks.check_antigravity_fanout(vault, home)
    assert outcome.severity == Severity.BROKEN
    assert "antigravity-ide" in outcome.message
    assert outcome.remedy is not None


def test_fanout_remedy_reruns_the_render_and_keeps_the_copys_servers(tmp_path):
    vault, home, canonical, _ = _mcp_setup(tmp_path)
    stale = _consumer(home, "antigravity-ide")
    stale.write_text(json.dumps({"mcpServers": {"hand-added": {"command": "mine", "args": [], "env": {}}}}))
    outcome = mcp_checks.check_antigravity_fanout(vault, home)
    assert outcome.remedy() is True
    assert "hand-added" in json.loads(canonical.read_text(encoding="utf-8"))["mcpServers"]
    assert mcp_checks.check_antigravity_fanout(vault, home).severity == Severity.OK


def test_fanout_undetermined_when_antigravity_never_rendered(tmp_path):
    vault, home = tmp_path / "vault", tmp_path / "home"
    vault.mkdir()
    home.mkdir()
    assert mcp_checks.check_antigravity_fanout(vault, home).severity == Severity.UNDETERMINED


def test_fanout_ignores_a_consumer_directory_that_was_never_created(tmp_path):
    vault, home, canonical, _ = _mcp_setup(tmp_path)
    # Only antigravity-cli exists here; the other two were never launched, so there is nothing to compare.
    _consumer(home, "antigravity-cli").write_bytes(canonical.read_bytes())
    assert mcp_checks.check_antigravity_fanout(vault, home).severity == Severity.OK
