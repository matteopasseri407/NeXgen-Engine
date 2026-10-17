"""Pinning dello split profilo/remotes di first_run prima del refactor.

Fissa il comportamento attuale (non quello desiderato): write_user_profile
riempie il profilo atteso, write_remotes scrive i remotes attesi, la seconda
esecuzione e idempotente. Solo tmp_path, nessuna rete, nessuna home vera.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
REPO_ROOT = SCRIPTS_DIR.parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.first_run import (  # noqa: E402
    write_remotes,
    write_user_profile,
)


def _vault(tmp_path: Path) -> Path:
    """Un vault come quello che l'utente si ritrova appena clonato."""
    vault = tmp_path / "vault"
    (vault / "99-INDEX").mkdir(parents=True, exist_ok=True)
    (vault / "99-INDEX" / "USER-PROFILE.md").write_text(
        (REPO_ROOT / "99-INDEX" / "USER-PROFILE.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return vault


def test_write_user_profile_creates_expected_profile(tmp_path):
    vault = _vault(tmp_path)

    written, message = write_user_profile(
        vault, profile="MINIMAL", clis=["claude"], machines="primary (this one)"
    )

    assert written, message
    text = (vault / "99-INDEX" / "USER-PROFILE.md").read_text(encoding="utf-8")
    assert "- **profile**: `MINIMAL`" in text
    assert "- **clis**: `claude`" in text
    assert "- **sync_method**: `manual`" in text
    assert "[MINIMAL | MULTI]" not in text
    assert "NOTE FOR THE INSTALLER AGENT" not in text
    assert str(vault) in text


def test_write_remotes_writes_expected_remotes(tmp_path):
    vault = _vault(tmp_path)

    written, message = write_remotes(vault, profile="MINIMAL")

    assert written, message
    text = (
        vault / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    ).read_text(encoding="utf-8")
    assert "schema_version: 1" in text
    assert "authoritative_remote: local" in text
    assert "mirrors: []" in text


def test_second_run_is_idempotent(tmp_path):
    vault = _vault(tmp_path)
    write_user_profile(
        vault, profile="MINIMAL", clis=["claude"], machines="primary (this one)"
    )
    write_remotes(vault, profile="MINIMAL")
    profile_path = vault / "99-INDEX" / "USER-PROFILE.md"
    remotes_path = (
        vault / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    )
    profile_before = profile_path.read_text(encoding="utf-8")
    remotes_before = remotes_path.read_text(encoding="utf-8")

    written_profile, _ = write_user_profile(
        vault, profile="MULTI", clis=["opencode"], machines="altra"
    )
    written_remotes, _ = write_remotes(vault, profile="MULTI")

    assert not written_profile
    assert not written_remotes
    assert profile_path.read_text(encoding="utf-8") == profile_before
    assert remotes_path.read_text(encoding="utf-8") == remotes_before


def test_public_signatures_unchanged():
    assert list(inspect.signature(write_user_profile).parameters) == [
        "vault_root",
        "profile",
        "clis",
        "machines",
    ]
    assert list(inspect.signature(write_remotes).parameters) == [
        "vault_root",
        "profile",
    ]


def test_profile_and_remotes_helpers_in_sequence():
    import nexgen_core.first_run as first_run_mod

    src = Path(first_run_mod.__file__).read_text(encoding="utf-8")
    assert "def _write_profile" in src
    assert "def _write_remotes_body" in src
    assert src.index("def _write_profile") < src.index("def _write_remotes_body")
    profile_pos = src.index("def write_user_profile")
    remotes_pos = src.index("def write_remotes")
    assert "_write_profile(" in src[profile_pos:remotes_pos]
    assert "_write_remotes_body(" in src[remotes_pos:]
