"""SOUL.md is generated from its one source, never a second editable copy.

OpenClaw reads the agent's identity from `SOUL.md`; the engine keeps it in
`agent-self.md`. While the setup wrote that file only when it was missing, the two
drifted apart the moment somebody edited the one inside the workspace, and the
agent became a different person to the other CLIs. These tests pin the generation:
the body of the source, without its YAML header, written on every run.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "03-INFRA" / "scripts"))

from nexgen_core.openclaw.setup_agents import prepare  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.squad import get_squad_souls, read_soul_source  # noqa: E402 - sys.path shim for cloned checkout

PERSONAL = """---
status: active
---

# Spazio personale

Mi chiamo Kurisu.
Parlo in prima persona al femminile.
"""


def _vault_with_identity(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "03-INFRA" / "agent-universal-layer").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "agent-self.md").write_text(PERSONAL, encoding="utf-8")
    return vault


def test_the_header_is_bookkeeping_and_never_reaches_the_agent(tmp_path):
    vault = _vault_with_identity(tmp_path)
    body = read_soul_source(vault / "03-INFRA" / "agent-universal-layer" / "agent-self.md")

    assert body is not None
    assert "status: active" not in body
    assert body.startswith("# Spazio personale")
    assert body.endswith("\n")


def test_a_squad_that_names_a_file_gets_its_body_not_a_copy(tmp_path):
    vault = _vault_with_identity(tmp_path)
    squad_file = tmp_path / "squad.yaml"
    squad = {
        "agents": {
            "main": {"soul_from": "vault/03-INFRA/agent-universal-layer/agent-self.md"},
            "capo": {"soul": "# Capo\n\nTesto scritto nella squadra.\n"},
        }
    }

    souls = get_squad_souls(squad, squad_file)

    assert "status: active" not in souls["main"]
    assert souls["main"].startswith("# Spazio personale")
    assert souls["capo"] == "# Capo\n\nTesto scritto nella squadra.\n"


def test_a_missing_source_is_not_an_empty_soul(tmp_path):
    squad = {"agents": {"main": {"soul_from": "non/esiste.md"}}}

    assert get_squad_souls(squad, tmp_path / "squad.yaml") == {}


def test_the_workspace_soul_is_rewritten_when_the_source_changes(tmp_path):
    vault = _vault_with_identity(tmp_path)
    squad_file = tmp_path / "squad.yaml"
    squad = {"agents": {"main": {"soul_from": "vault/03-INFRA/agent-universal-layer/agent-self.md"}}}
    souls = get_squad_souls(squad, squad_file)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("una copia vecchia che qualcuno ha modificato\n", encoding="utf-8")

    prepare({"main": workspace}, set(), "it", lambda _: None, souls=souls)

    assert (workspace / "SOUL.md").read_text(encoding="utf-8") == souls["main"]


def test_an_agent_with_no_source_keeps_the_file_it_has(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("mio\n", encoding="utf-8")

    prepare({"main": workspace}, set(), "it", lambda _: None, souls={})

    assert (workspace / "SOUL.md").read_text(encoding="utf-8") == "mio\n"
