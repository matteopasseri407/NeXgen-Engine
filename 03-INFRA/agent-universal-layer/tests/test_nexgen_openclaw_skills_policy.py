"""What OpenClaw may put in its prompt, derived from the manifest that declares exposure.

OpenClaw writes every skill it finds into the system prompt of every session. The
engine's policy is the opposite: a body is read when it is used, and `exposure:
manual` says so. OpenClaw has `skills_read` for the on-demand half, so the two
agree by turning off the prompt half rather than by removing anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "03-INFRA" / "scripts"))

from nexgen_core.openclaw.skills_policy import skill_entries, with_skill_policy  # noqa: E402 - sys.path shim for cloned checkout

MANIFEST = """skills:
  humanizer:
    origin: local
    exposure: manual
    description: riscrive il testo
  vault-save:
    origin: local
    exposure: core
    description: salva una nota
  senza-esposizione:
    origin: local
    description: nessuna esposizione dichiarata
"""


def _vault(tmp_path: Path, manifest: str = MANIFEST) -> Path:
    vault = tmp_path / "vault"
    target = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    target.mkdir(parents=True)
    (target / "skills.manifest.yaml").write_text(manifest, encoding="utf-8")
    return vault


def test_only_the_core_skills_are_enabled_for_the_prompt(tmp_path):
    entries = skill_entries(_vault(tmp_path))

    assert entries["vault-save"] == {"enabled": True}
    assert entries["humanizer"] == {"enabled": False}
    assert entries["senza-esposizione"] == {"enabled": False}


def test_a_vault_without_a_manifest_decides_nothing(tmp_path):
    assert skill_entries(tmp_path / "vuoto") == {}


def test_a_skill_the_manifest_does_not_know_keeps_what_it_has(tmp_path):
    config = {"skills": {"entries": {"installata-da-openclaw": {"enabled": True}}}}

    merged = with_skill_policy(config, _vault(tmp_path))

    assert merged["skills"]["entries"]["installata-da-openclaw"] == {"enabled": True}
    assert merged["skills"]["entries"]["vault-save"] == {"enabled": True}
    assert merged["skills"]["entries"]["humanizer"] == {"enabled": False}


def test_the_config_is_left_alone_when_nothing_is_declared(tmp_path):
    config = {"skills": {"entries": {"x": {"enabled": True}}}}

    assert with_skill_policy(config, tmp_path / "vuoto") == config
