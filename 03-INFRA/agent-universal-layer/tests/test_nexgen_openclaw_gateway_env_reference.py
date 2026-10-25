"""`openclaw mcp serve` authenticates from its own config: a reference stays a reference.

OpenClaw reads the gateway URL and token from `openclaw.json`. The engine must not hand
a CLI an env block built from whatever this shell happens to export: a `${NAME}` left to
`os.environ.get` becomes an empty string when the variable is not in the engine's
environment, so every CLI would start `openclaw mcp serve` with an empty URL and an empty
token. The gateway refuses that (it demands a token as soon as a URL is given) and the
engine cannot tell it apart from "not configured".

A reference reaches OpenClaw as written, and OpenClaw substitutes its own value or falls
back to its local configuration.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

UL = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = UL.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.renderer import McpRenderer  # noqa: E402

MANIFEST = UL / "mcp" / "manifest.yaml"

#: A manifest that declares what the engine itself never should: gateway variables.
OPENCLAW_WITH_ENV = """schema_version: 1
servers:
  openclaw:
    exposure: eager
    transport: stdio
    command: openclaw
    args: ["mcp", "serve"]
    targets: [claude, codex, antigravity, opencode]
    enabled: true
    env:
      OPENCLAW_GATEWAY_URL: "${OPENCLAW_GATEWAY_URL}"
      OPENCLAW_GATEWAY_TOKEN: "${OPENCLAW_GATEWAY_TOKEN}"
"""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A vault that carries that manifest, and a home that is nobody's."""
    folder = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer" / "mcp"
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text(OPENCLAW_WITH_ENV, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    return tmp_path / "vault", home


@pytest.mark.parametrize("cli", ["claude", "codex", "antigravity", "opencode"])
def test_a_gateway_reference_is_kept_instead_of_becoming_an_empty_value(machine, cli: str) -> None:
    """An empty string is not "no value": it is a value the gateway refuses."""
    vault, home = machine

    resolved = McpRenderer(vault_data=vault, home=home).load_resolved_servers(cli)

    assert "openclaw" in resolved
    env = resolved["openclaw"]["env"]
    assert env["OPENCLAW_GATEWAY_URL"] == "${OPENCLAW_GATEWAY_URL}"
    assert env["OPENCLAW_GATEWAY_TOKEN"] == "${OPENCLAW_GATEWAY_TOKEN}"


def test_the_engine_manifest_declares_no_gateway_env_for_that_server() -> None:
    """The canonical entry needs none: `openclaw mcp serve` reads its own configuration."""
    text = MANIFEST.read_text(encoding="utf-8")

    assert "OPENCLAW_GATEWAY_TOKEN" not in text
    assert "OPENCLAW_GATEWAY_URL" not in text
