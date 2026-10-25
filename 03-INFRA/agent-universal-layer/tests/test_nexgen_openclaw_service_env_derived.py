"""The gateway's environment is a derivative of the secret store, never a second one.

OpenClaw reads its variables from `$OPENCLAW_STATE_DIR/.env`. The setup used to add
the missing keys and leave the present ones alone, so a token rotated in the engine's
encrypted store kept answering from the old value here: the same secret alive twice,
which is what the store exists to prevent.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.setup_service_env import write_gateway_service_env  # noqa: E402 - sys.path shim for cloned checkout


@dataclass
class FakeSetup:
    """The two fields the writer reads, and the words it says."""

    state_dir: Path
    env: dict[str, str]
    config_path: Path
    said: list[str]

    def out(self, line: str) -> None:
        self.said.append(line)


def _setup(tmp_path: Path, env: dict[str, str]) -> FakeSetup:
    state = tmp_path / ".openclaw"
    state.mkdir()
    config = state / "openclaw.json"
    config.write_text("{}", encoding="utf-8")
    said: list[str] = []
    return FakeSetup(state_dir=state, env=env, config_path=config, said=said)


def test_a_rotated_secret_replaces_the_old_value(tmp_path):
    setup = _setup(tmp_path, {"GITHUB_PERSONAL_ACCESS_TOKEN": "nuovo-valore"})
    (setup.state_dir / ".env").write_text(
        'GITHUB_PERSONAL_ACCESS_TOKEN="vecchio-valore"\nALTRA="resta"\n', encoding="utf-8"
    )

    write_gateway_service_env(setup, {"mcp": {"servers": {"x": {"env": {"TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"}}}}})

    written = (setup.state_dir / ".env").read_text(encoding="utf-8")
    assert "nuovo-valore" in written
    assert "vecchio-valore" not in written
    # A key the engine does not reference is the person's and stays as it was.
    assert 'ALTRA="resta"' in written


def test_the_value_never_reaches_the_output(tmp_path):
    setup = _setup(tmp_path, {"SECRET_TOKEN": "valore-che-non-si-stampa"})

    write_gateway_service_env(setup, {"mcp": {"servers": {"x": {"env": {"TOKEN": "${SECRET_TOKEN}"}}}}})

    assert all("valore-che-non-si-stampa" not in line for line in setup.said)
    assert (setup.state_dir / ".env").is_file()
