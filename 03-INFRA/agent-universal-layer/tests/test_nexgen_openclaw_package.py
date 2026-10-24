"""The `nexgen` plugin as a package: one file list, one reproducible tarball, no private bytes.

Every test builds into a temporary home and a temporary hooks folder, and none of them runs
`openclaw`: the owner's `~/.openclaw`, the real state folder and a real gateway are never
touched.
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest
from conftest import REAL_UL
from nexgen_core import __version__
from nexgen_core.errors import NexgenError
from nexgen_core.openclaw.plugin_package import build_package
from nexgen_core.paths import openclaw_config, resolve_state_dir
from nexgen_core.runtimes.base import GUARDRAIL_CORE_NAME, GUARDRAIL_SIDECAR_NAME
from nexgen_core.runtimes.openclaw import (
    BRIEFING_NAME,
    PLUGIN_CONFIG_NAME,
    PLUGIN_ID,
    OpenClawRuntime,
    plugin_files,
)

HOOKS = REAL_UL / "hooks"
PLUGIN_SOURCE = HOOKS / "openclaw-guardrail-plugin"


def a_home(tmp_path: Path) -> Path:
    """A home whose OpenClaw already has a state folder: that is where the deposit goes."""
    home = tmp_path / "home"
    config = openclaw_config(home)
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"gateway": {"port": 18789}}), encoding="utf-8")
    return home


def a_policy_body(tmp_path: Path) -> Path:
    body = tmp_path / "guardrail-body.mjs"
    body.write_text("// the owner's private policy, in the test only\n", encoding="utf-8")
    return body


def fake_hooks(tmp_path: Path) -> Path:
    """A copy of the engine's hooks folder, so a test can add to it or take from it."""
    engine_hooks = tmp_path / "engine-hooks"
    engine_hooks.mkdir()
    shutil.copytree(PLUGIN_SOURCE, engine_hooks / PLUGIN_SOURCE.name)
    shutil.copy2(HOOKS / GUARDRAIL_CORE_NAME, engine_hooks / GUARDRAIL_CORE_NAME)
    shutil.copy2(HOOKS / BRIEFING_NAME, engine_hooks / BRIEFING_NAME)
    return engine_hooks


def archive_members(result) -> dict[str, bytes]:
    """Every member of the built tarball, by the name npm shows for it."""
    with tarfile.open(result.path, mode="r:gz") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def test_the_deposit_writes_exactly_the_files_on_the_plugins_one_list(tmp_path):
    home = a_home(tmp_path)

    OpenClawRuntime().install_guardrail(home, a_policy_body(tmp_path), HOOKS)

    deposited = {
        path.name: path
        for path in (home / ".openclaw" / "extensions" / PLUGIN_ID).iterdir()
        if path.is_file()
    }
    engine_owned = {PLUGIN_CONFIG_NAME, GUARDRAIL_SIDECAR_NAME}
    listed = plugin_files(HOOKS)

    assert set(deposited) - engine_owned == set(listed)
    for name, source in listed.items():
        assert deposited[name].read_bytes() == source.read_bytes(), name


# ---------------------------------------------------------------- what is inside


def test_the_package_carries_exactly_the_public_files_the_deposit_writes(tmp_path):
    home = a_home(tmp_path)
    OpenClawRuntime().install_guardrail(home, a_policy_body(tmp_path), HOOKS)

    result = build_package(home=home, engine_hooks_dir=HOOKS)

    deposited = {path.name for path in (home / ".openclaw" / "extensions" / PLUGIN_ID).iterdir() if path.is_file()}
    assert set(result.files) == deposited - {PLUGIN_CONFIG_NAME, GUARDRAIL_SIDECAR_NAME}
    members = archive_members(result)
    assert set(members) == {f"package/{name}" for name in result.files}
    assert members["package/index.mjs"] == (PLUGIN_SOURCE / "index.mjs").read_bytes()


def test_package_json_is_what_openclaw_reads_before_it_loads_any_code(tmp_path):
    result = build_package(home=tmp_path, engine_hooks_dir=HOOKS)

    members = archive_members(result)
    package = json.loads(members["package/package.json"])
    assert package["name"] == PLUGIN_ID
    assert package["version"] == __version__
    assert package["type"] == "module"
    assert package["openclaw"]["extensions"] == ["./index.mjs"]
    # Built to be installed, never published: the source's own guard travels unchanged.
    assert package["private"] is True
    # OpenClaw reads the manifest to validate the configuration without running the plugin.
    assert json.loads(members["package/openclaw.plugin.json"])["id"] == PLUGIN_ID


def test_the_private_policy_and_the_engine_switches_never_enter_the_package(tmp_path):
    hooks = fake_hooks(tmp_path)
    plugin_dir = hooks / PLUGIN_SOURCE.name
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text('{"posture": "bypass"}', encoding="utf-8")
    (plugin_dir / GUARDRAIL_SIDECAR_NAME).write_text('{"hooks": []}', encoding="utf-8")
    (plugin_dir / "hooks").mkdir()
    (plugin_dir / "hooks" / "private-body.mjs").write_text("// the owner's policy\n", encoding="utf-8")

    members = archive_members(build_package(home=tmp_path, engine_hooks_dir=hooks))

    private = {PLUGIN_CONFIG_NAME, GUARDRAIL_SIDECAR_NAME}
    assert private.isdisjoint(Path(name).name for name in members)
    # Nothing nested either: the policy body lives in the plugin's own `hooks/` folder.
    assert all(name.count("/") == 1 for name in members)
    # And the mechanism is still there: excluding the private files dropped nothing else.
    assert "package/index.mjs" in members


# ------------------------------------------------------------------ reproducibility


def test_two_builds_of_the_same_files_are_byte_for_byte_identical(tmp_path):
    hooks = fake_hooks(tmp_path)
    first = build_package(home=tmp_path, engine_hooks_dir=hooks, output_dir=tmp_path / "first")
    for path in hooks.rglob("*"):
        if path.is_file():
            os.utime(path, (1_700_000_000, 1_700_000_000))

    second = build_package(home=tmp_path, engine_hooks_dir=hooks, output_dir=tmp_path / "second")

    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()


def test_source_dates_and_permissions_never_reach_the_archive(tmp_path):
    hooks = fake_hooks(tmp_path)
    for path in hooks.rglob("*"):
        if path.is_file():
            os.utime(path, (1_700_000_000, 1_700_000_000))

    result = build_package(home=tmp_path, engine_hooks_dir=hooks)

    with tarfile.open(result.path, mode="r:gz") as archive:
        for member in archive.getmembers():
            assert (member.mtime, member.mode, member.uid, member.gid) == (0, 0o644, 0, 0)
            assert (member.uname, member.gname) == ("", "")


# ------------------------------------------------------------------- where it lands


def test_the_tarball_lands_in_the_engines_state_folder(tmp_path):
    result = build_package(home=tmp_path, engine_hooks_dir=HOOKS)

    assert result.path.parent == resolve_state_dir(tmp_path) / "openclaw" / "plugin"
    assert result.path.name == f"{PLUGIN_ID}-{__version__}.tgz"
    assert len(result.sha256) == 64


@pytest.mark.parametrize("missing", [
    Path(PLUGIN_SOURCE.name) / "package.json",
    Path(GUARDRAIL_CORE_NAME),
    Path(BRIEFING_NAME),
])
def test_an_incomplete_plugin_is_refused_instead_of_half_packaged(tmp_path, missing):
    hooks = fake_hooks(tmp_path)
    (hooks / missing).unlink()

    with pytest.raises(NexgenError):
        build_package(home=tmp_path, engine_hooks_dir=hooks)
