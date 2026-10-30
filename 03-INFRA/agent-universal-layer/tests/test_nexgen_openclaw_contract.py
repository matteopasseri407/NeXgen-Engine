"""The configuration the engine writes is one the pinned OpenClaw accepts.

Every other OpenClaw test judges the engine's output against what the engine
believes OpenClaw wants. Twice that belief was wrong and shipped: a squad's
`models` went into `models.entries`, which OpenClaw refuses with the whole file,
and a `claude-cli` agent was given connectors in a file OpenClaw never lets it
read. Here the judge is OpenClaw itself: `openclaw config validate` on the file
the engine produces, from the profile, the example squad and the MCP render.

Without an `openclaw` binary the test skips. The CI job that installs the pinned
release sets `NEXGEN_REQUIRE_OPENCLAW=1`, which turns that skip into a failure,
so the gate can never pass by absence.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import build_config  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.setup_write import merged_document  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.squad import apply_squad, load_squad  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.renderer import McpRenderer  # noqa: E402 - sys.path shim for cloned checkout

REPO = Path(__file__).resolve().parents[3]
LAYER = REPO / "03-INFRA" / "agent-universal-layer"

#: Every variable a manifest server is gated on, so the render mounts all of them.
GATES = ("FIRECRAWL_API_KEY", "N8N_MCP_TOKEN", "VAULT_LIBRARY_URL", "VAULT_LIBRARY_TOKEN",
         "OCR_TUNNEL_PORT", "MCP_MEMORY_OPT_IN")


#: The CI job installs the release it means to judge (the pin, or the latest one) and sets this.
REQUIRED = os.environ.get("NEXGEN_REQUIRE_OPENCLAW") == "1"
PINNED = json.loads((LAYER / "openclaw" / "pin.json").read_text(encoding="utf-8"))["version"]


def _pinned_binary() -> str | None:
    """The `openclaw` on PATH when it is the pinned release; an older one would judge an old schema."""
    found = shutil.which("openclaw")
    if not found:
        return None
    try:
        version = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=60,
                                 check=False).stdout
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a CLI that cannot answer is not the judge"
        return None
    return found if PINNED in version else None


OPENCLAW = shutil.which("openclaw") if REQUIRED else _pinned_binary()
WHY_SKIPPED = f"no openclaw {PINNED} on PATH: the contract is judged where the pinned release is installed"


def _openclaw() -> str:
    if not OPENCLAW:
        pytest.fail("NEXGEN_REQUIRE_OPENCLAW=1 but no openclaw binary is on PATH")
    return OPENCLAW


def _machine(config_path: Path) -> Detection:
    """A machine with every subscription and a local model: the widest profile."""
    return Detection(
        openclaw=Installation(installed=True, version="pinned", config_path=str(config_path)),
        claude=Login(binary=True, login=True),
        codex=Login(binary=True, login=True),
        opencode_go=True,
        local=LocalModels(ollama=(LocalModelInfo(name="qwen3:8b:latest"),), llamacpp=()),
        n8n=True,
    )


def _validate(openclaw: str, home: Path, config: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home), "OPENCLAW_STATE_DIR": str(config.parent),
           "OPENCLAW_CONFIG_PATH": str(config)}
    return subprocess.run([openclaw, "config", "validate"], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, check=False)


def _engine_document(config: Path) -> dict:
    proposed = build_config(_machine(config))
    squad = load_squad(LAYER / "squad.example.yaml")
    assert squad, "the example squad must load"
    return merged_document(None, apply_squad(proposed, squad))


@pytest.mark.skipif(OPENCLAW is None and not REQUIRED, reason=WHY_SKIPPED)
def test_the_profile_and_the_example_squad_are_valid_for_the_pinned_openclaw(tmp_path: Path) -> None:
    openclaw = _openclaw()
    config = tmp_path / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(_engine_document(config), indent=2), encoding="utf-8")

    result = _validate(openclaw, tmp_path, config)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(OPENCLAW is None and not REQUIRED, reason=WHY_SKIPPED)
def test_the_mcp_render_with_the_gateway_is_valid_for_the_pinned_openclaw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    openclaw = _openclaw()
    for name in GATES:
        monkeypatch.setenv(name, "contract-placeholder")
    config = tmp_path / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(_engine_document(config), indent=2), encoding="utf-8")
    renderer = McpRenderer(vault_data=REPO, engine_root=REPO / "03-INFRA", home=tmp_path)

    ok, message = renderer.render_openclaw(write=True)
    assert ok, message
    servers = json.loads(config.read_text(encoding="utf-8"))["mcp"]["servers"]
    assert "lazy-mcp" in servers

    result = _validate(openclaw, tmp_path, config)

    assert result.returncode == 0, result.stdout + result.stderr
