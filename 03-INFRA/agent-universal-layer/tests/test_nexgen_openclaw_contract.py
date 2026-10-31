"""The configuration the engine writes is one the pinned OpenClaw accepts.

Every other OpenClaw test judges the engine's output against what the engine
believes OpenClaw wants. Twice that belief was wrong and shipped: a squad's
`models` went into `models.entries`, which OpenClaw refuses with the whole file,
and a `claude-cli` agent was given connectors in a file OpenClaw never lets it
read. Here the judge is OpenClaw itself: `openclaw config validate` on the file
the engine produces, from the profile, the example squad and the MCP render.

Validation is not the whole contract: OpenClaw also repairs the file. The last
test runs `openclaw doctor --fix` on the same document and requires every leaf the
engine proposed to survive untouched, and the engine's next merge to agree with
the repaired file -- the engine fills gaps through OpenClaw's own doors and never
fights it about a value.

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


def _env(home: Path, config: Path) -> dict[str, str]:
    """The environment a run of the pinned binary sees: this file, in this home, and nothing else."""
    return {**os.environ, "HOME": str(home), "OPENCLAW_STATE_DIR": str(config.parent),
            "OPENCLAW_CONFIG_PATH": str(config)}


def _validate(openclaw: str, home: Path, config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([openclaw, "config", "validate"], env=_env(home, config), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180, check=False)


#: In a fresh home, doctor installs configured plugins from npm and migrates their state.
DOCTOR_TIMEOUT_S = 600


def _doctor(openclaw: str, home: Path, config: Path) -> subprocess.CompletedProcess[str]:
    """`openclaw doctor --fix`: the run whose repairs the engine must not have to undo."""
    return subprocess.run([openclaw, "doctor", "--fix", "--non-interactive", "--no-workspace-suggestions"],
                          env=_env(home, config), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=DOCTOR_TIMEOUT_S, check=False)


def _engine_proposal(config: Path) -> tuple[dict, dict]:
    """The proposal a setup on this machine builds, and the squad it applies."""
    proposed = build_config(_machine(config))
    squad = load_squad(LAYER / "squad.example.yaml")
    assert squad, "the example squad must load"
    return proposed, squad


def _engine_document(config: Path) -> dict:
    proposed, squad = _engine_proposal(config)
    return merged_document(None, apply_squad(proposed, squad))


def _engine_owned_leaves(document: dict) -> dict[tuple[str, ...], object]:
    """Every leaf the engine proposed, keyed by its path.

    A dict is walked into; a scalar or a list is a leaf, because the engine writes whole
    lists (the model policy, a provider's model array) and OpenClaw rewriting one item
    inside them is the same disagreement as a changed scalar. Anything OpenClaw adds at a
    path the engine never wrote is not in this map, and is allowed.
    """
    leaves: dict[tuple[str, ...], object] = {}

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, path + (str(key),))
        else:
            leaves[path] = value

    walk(document, ())
    return leaves


def _at(document: object, path: tuple[str, ...]) -> object:
    """The value at `path`, or a marker when OpenClaw deleted the path entirely."""
    value = document
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return "<MISSING>"
        value = value[key]
    return value


@pytest.mark.skipif(OPENCLAW is None and not REQUIRED, reason=WHY_SKIPPED)
def test_the_profile_and_the_example_squad_are_valid_for_the_pinned_openclaw(tmp_path: Path) -> None:
    openclaw = _openclaw()
    config = tmp_path / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(_engine_document(config), indent=2), encoding="utf-8")

    result = _validate(openclaw, tmp_path, config)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(OPENCLAW is None and not REQUIRED, reason=WHY_SKIPPED)
def test_openclaw_doctor_leaves_the_engine_document_alone_and_the_next_merge_agrees(
    tmp_path: Path,
) -> None:
    """The engine fills gaps and never fights OpenClaw: one cycle, and no flip-flop.

    `doctor --fix` is the other author of this file. Whatever it repairs -- the canonical
    provider refs, the declared system primary, the channel plugin, the Ollama context --
    must be the shape the engine already proposed, or the two rewrite each other on every
    run. The second half is the next cycle: merging the same proposal into the repaired
    file changes nothing, so a setup after a doctor run is not a third version of the file.
    """
    openclaw = _openclaw()
    config = tmp_path / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    proposed, squad = _engine_proposal(config)
    engine = merged_document(None, apply_squad(proposed, squad))
    config.write_text(json.dumps(engine, indent=2), encoding="utf-8")

    result = _doctor(openclaw, tmp_path, config)
    assert result.returncode == 0, result.stdout + result.stderr
    after = json.loads(config.read_text(encoding="utf-8"))

    for path, value in _engine_owned_leaves(engine).items():
        assert _at(after, path) == value, (
            f"openclaw doctor --fix changed an engine-owned leaf: {'.'.join(path)} "
            f"was {value!r}, is {_at(after, path)!r}"
        )

    # A plugin entry the engine writes without `enabled` is a key OpenClaw adds inside a
    # path the engine owns, which the leaf walk above cannot see. The engine says it in the
    # proposal, and the walk then proves the value stands after the repair.
    for name, entry in engine["plugins"]["entries"].items():
        assert entry.get("enabled") is True, (
            f"the engine allows the '{name}' plugin without switching it on"
        )

    next_cycle = merged_document(after, apply_squad(proposed, squad))
    for path in _engine_owned_leaves(engine):
        assert _at(next_cycle, path) == _at(after, path), (
            f"the next merge would revert OpenClaw's file at {'.'.join(path)}: "
            f"the file says {_at(after, path)!r}, the merge writes {_at(next_cycle, path)!r}"
        )


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
