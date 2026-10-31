"""The systemd units the guard installs must name the machine's real engine and credentials.

On a headless server the engine lived in `~/KnowledgeVault/03-INFRA` while the
unit recorded the fallback `~/.nexgen-engine/03-INFRA`, so every guard cycle
rewrote configuration with paths that do not exist. And the unit started with
none of the connector environment, so every server whose manifest entry has a
`require_env` gate dropped out of the render.

Two more things are pinned here: the unit's PATH follows nvm's default Node
instead of the version an earlier unit happened to declare first, and the doctor
notices a unit whose engine folder has since been moved or deleted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import scheduler  # noqa: E402
from nexgen_core.checks import host_checks  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

BUILDERS = ("_systemd_service_content", "_systemd_heartbeat_content")


@pytest.mark.parametrize("builder", BUILDERS)
def test_the_unit_records_the_engine_root_actually_in_use(tmp_path: Path, builder: str) -> None:
    """The engine root used to be left out when it matched the vault's layer, so the
    guard fell back to `~/.nexgen-engine/03-INFRA` at the next cycle."""
    home = tmp_path / "home"
    vault = tmp_path / "KnowledgeVault"
    engine = vault / "03-INFRA"

    text = getattr(scheduler, builder)(home, engine, vault, vault)

    # systemd.syntax(7) doubles a backslash inside a quoted value; a Linux path has none.
    quoted = str(engine).replace("\\", "\\\\")
    assert f'Environment="AGENT_ENGINE_ROOT={quoted}"' in text


@pytest.mark.parametrize("builder", BUILDERS)
def test_the_unit_loads_the_connector_environment_before_the_guard_runs(tmp_path: Path, builder: str) -> None:
    home = tmp_path / "home"

    text = getattr(scheduler, builder)(
        home, tmp_path / "engine" / "03-INFRA", tmp_path / "vault", tmp_path / "vault",
    )

    assert f"EnvironmentFile=-{home / '.openclaw' / '.env'}" in text
    # The shell deposit is not systemd syntax: its `export` lines would be skipped.
    assert "secrets.env" not in text


# ------------------------------------------------------- the PATH of the unit


def _nvm_tree(home: Path, versions: tuple[str, ...]) -> dict[str, Path]:
    """`~/.nvm/versions/node/<v>/bin` for each version, as nvm lays them out."""
    bins: dict[str, Path] = {}
    for version in versions:
        path = home / ".nvm" / "versions" / "node" / f"v{version}" / "bin"
        path.mkdir(parents=True)
        bins[version] = path
    return bins


def _write_default_alias(home: Path, value: str) -> None:
    alias = home / ".nvm" / "alias"
    alias.mkdir(parents=True, exist_ok=True)
    (alias / "default").write_text(value + "\n", encoding="utf-8")


def _write_unit_path(home: Path, path: str) -> None:
    """The unit as the previous cycle left it: the one place the old PATH survives."""
    unit = home / ".config" / "systemd" / "user" / "agent-sync.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Service]\n" + scheduler._systemd_env_line("PATH", path) + "\n", encoding="utf-8")


def test_the_unit_path_follows_nvm_default_and_drops_the_other_nodes(tmp_path: Path, monkeypatch) -> None:
    """After `nvm alias default`, the old version's bin must not stay ahead of the new one."""
    home = tmp_path / "home"
    bins = _nvm_tree(home, ("22.1.0", "24.22.1"))
    _write_default_alias(home, "24.22.1")
    monkeypatch.setattr(scheduler, "_tool_dirs", lambda: [str(bins["22.1.0"]), "/opt/other/bin"])
    _write_unit_path(home, os.pathsep.join([str(bins["22.1.0"]), "/usr/bin"]))

    entries = scheduler._scheduler_path(home).split(os.pathsep)

    assert str(bins["24.22.1"]) in entries
    assert str(bins["22.1.0"]) not in entries
    assert "/opt/other/bin" in entries


def test_the_unit_path_is_a_fixed_point_of_itself(tmp_path: Path, monkeypatch) -> None:
    """Two runs produce the same unit: the guard must not rewrite and reload it every cycle."""
    home = tmp_path / "home"
    _nvm_tree(home, ("22.1.0", "24.22.1"))
    _write_default_alias(home, "v24.22.1")
    monkeypatch.setattr(scheduler, "_tool_dirs", lambda: ["/opt/other/bin"])

    first = scheduler._scheduler_path(home)
    _write_unit_path(home, first)
    second = scheduler._scheduler_path(home)

    assert second == first


def test_an_alias_naming_another_alias_is_followed_one_level(tmp_path: Path) -> None:
    """`nvm alias default lts/*` writes an alias, not a version."""
    home = tmp_path / "home"
    bins = _nvm_tree(home, ("24.22.1",))
    _write_default_alias(home, "lts")
    (home / ".nvm" / "alias" / "lts").write_text("v24.22.1\n", encoding="utf-8")

    entries = scheduler._scheduler_path(home).split(os.pathsep)

    assert str(bins["24.22.1"]) in entries


def test_an_alias_that_names_no_installed_version_keeps_todays_behaviour(tmp_path: Path, monkeypatch) -> None:
    """Inventing a Node the machine does not have is worse than a stale order."""
    home = tmp_path / "home"
    old = tmp_path / "old-node" / "bin"
    old.mkdir(parents=True)
    _write_default_alias(home, "24.22.1")
    monkeypatch.setattr(scheduler, "_tool_dirs", lambda: [str(old)])

    entries = scheduler._scheduler_path(home).split(os.pathsep)

    assert str(old) in entries


# --------------------------------------------- the doctor and the unit's engine


def _unit_with_engine(home: Path, name: str, engine: Path) -> None:
    unit = home / ".config" / "systemd" / "user" / name
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Service]\n" + scheduler._systemd_env_line("AGENT_ENGINE_ROOT", str(engine)) + "\n",
                    encoding="utf-8")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the check reads Linux systemd units only")
def test_a_unit_whose_engine_folder_vanished_is_a_failure(tmp_path: Path) -> None:
    """A moved checkout leaves a unit that starts a Python which cannot find the engine."""
    home = tmp_path / "home"
    _unit_with_engine(home, "agent-sync.service", tmp_path / "moved-away")

    outcome = host_checks.check_unit_engine_root(home)

    assert outcome.severity is Severity.BROKEN
    assert "nexgen sync" in (outcome.action or "")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the check reads Linux systemd units only")
def test_a_unit_whose_engine_folder_exists_is_green(tmp_path: Path) -> None:
    home = tmp_path / "home"
    engine = tmp_path / "engine" / "03-INFRA"
    engine.mkdir(parents=True)
    _unit_with_engine(home, "agent-heartbeat.service", engine)

    assert host_checks.check_unit_engine_root(home).severity is Severity.OK


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the check reads Linux systemd units only")
def test_no_unit_is_not_a_failure(tmp_path: Path) -> None:
    """A machine without units has nothing to judge; the timer check owns that question."""
    assert host_checks.check_unit_engine_root(tmp_path / "home").severity is Severity.UNDETERMINED
