"""The lane reads notes and code, not the credential files that happen to sit beside them."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_local.config import LaneConfig  # noqa: E402
from nexgen_local.tools import ToolRegistry, is_sensitive_name  # noqa: E402


@pytest.mark.parametrize("name", [".env", ".env.production", ".netrc", ".npmrc", "tokens.json", "token.json",
                                  "credentials", "credentials.json", "secrets.yaml", "id_rsa", "id_ed25519",
                                  "server.pem", "signing.key", "bundle.p12", "vault.kdbx", "backup.age"])
def test_credential_files_are_recognised(name):
    assert is_sensitive_name(name)


@pytest.mark.parametrize("name", [".env.example", ".env.sample", "id_ed25519.pub", "README.md", "notes.md",
                                  "environment.md", "keyboard.md", "tokenizer.py", "secret-santa.md", "monkey.txt"])
def test_ordinary_files_and_documentation_are_not(name):
    assert not is_sensitive_name(name)


def test_a_repo_read_refuses_a_credential_file_and_reads_its_example(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("SERVICE_TOKEN=hunter2\n", encoding="utf-8")
    (repo / ".env.example").write_text("SERVICE_TOKEN=\n", encoding="utf-8")
    cfg = LaneConfig(vault_root=tmp_path / "vault", repo_roots=(repo,), audit_path=tmp_path / "audit.jsonl")
    tools = ToolRegistry(cfg)
    assert "hunter2" not in tools.read_repo(".env")
    assert "rifiutato" in tools.calls[-1].args.get("path", "") or tools.calls[-1].ok is False
    assert "SERVICE_TOKEN" in tools.read_repo(".env.example")
