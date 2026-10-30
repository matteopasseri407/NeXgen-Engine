"""The systemd units the guard installs must name the machine's real engine and credentials.

On a headless server the engine lived in `~/KnowledgeVault/03-INFRA` while the
unit recorded the fallback `~/.nexgen-engine/03-INFRA`, so every guard cycle
rewrote configuration with paths that do not exist. And the unit started with
none of the connector environment, so every server whose manifest entry has a
`require_env` gate dropped out of the render.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import scheduler  # noqa: E402

BUILDERS = ("_systemd_service_content", "_systemd_heartbeat_content")


@pytest.mark.parametrize("builder", BUILDERS)
def test_the_unit_records_the_engine_root_actually_in_use(tmp_path: Path, builder: str) -> None:
    """The engine root used to be left out when it matched the vault's layer, so the
    guard fell back to `~/.nexgen-engine/03-INFRA` at the next cycle."""
    home = tmp_path / "home"
    vault = tmp_path / "KnowledgeVault"
    engine = vault / "03-INFRA"

    text = getattr(scheduler, builder)(home, engine, vault, vault)

    assert f'Environment="AGENT_ENGINE_ROOT={engine}"' in text


@pytest.mark.parametrize("builder", BUILDERS)
def test_the_unit_loads_the_connector_environment_before_the_guard_runs(tmp_path: Path, builder: str) -> None:
    home = tmp_path / "home"

    text = getattr(scheduler, builder)(
        home, tmp_path / "engine" / "03-INFRA", tmp_path / "vault", tmp_path / "vault",
    )

    assert f"EnvironmentFile=-{home / '.openclaw' / '.env'}" in text
    # The shell deposit is not systemd syntax: its `export` lines would be skipped.
    assert "secrets.env" not in text
