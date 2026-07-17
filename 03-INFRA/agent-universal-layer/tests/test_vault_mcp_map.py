"""Behavioral tests for item 21 tranche B on the vault-library MCP server:
the write-time link advisory and the map_overview orientation tool.

The advisory is discipline-at-write: every write result gains an additive
`unresolved_links` field listing wikilink targets in the JUST-WRITTEN
content that resolve to nothing (with a relocation hint when a unique
basename match exists). It NEVER blocks the write — a forward link to a
note you will create next is a legitimate pattern in this vault model.

map_overview is the agent compass for probe-first: a token-bounded,
read-only structural summary (counts, top hubs, first broken links and
orphans) whose semantics deliberately match vault-map.py — generated
indexes never rescue orphans, 99-SECRETS/asset targets are never broken,
archive-sourced dead links are frozen history, fenced code is quotation.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="vault-mcp write path uses fcntl (POSIX-only, like the container)",
)

REPO = Path(__file__).resolve().parents[3]
VAULT_MCP_SRC = REPO / "03-INFRA" / "deploy" / "vault-mcp" / "src"
if str(VAULT_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(VAULT_MCP_SRC))

sys.dont_write_bytecode = True

from vault_mcp_server.config import Settings  # noqa: E402
from vault_mcp_server.vault import VaultService  # noqa: E402


def _git(root: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@localhost",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@localhost",
    }
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env
    )
    return result.stdout.strip()


def make_settings(root: Path, git_dir: Path | None, **overrides) -> Settings:
    values = dict(
        vault_root=root,
        vault_token=None,
        write_enabled=True,
        git_dir=git_dir,
        git_author_name="Vault MCP Test",
        git_author_email="vault-mcp-test@localhost",
        host="127.0.0.1",
        port=8081,
        mcp_path="/mcp",
        health_path="/healthz",
        stateless_http=True,
        json_response=True,
        allowed_origins=(),
        ignored_dirs=(".git", ".obsidian"),
        max_note_bytes=1_000_000,
        cache_ttl_seconds=0,
        default_search_limit=10,
        max_search_limit=25,
        start_here_filename="00-START-HERE.md",
        include_path_prefixes=(),
        exclude_path_prefixes=("99-SECRETS",),
        write_exclude_path_prefixes=("99-SECRETS", ".git"),
        max_write_bytes=262144,
        semantic_url=None,
        semantic_enabled=False,
        semantic_max_limit=5,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def vault(tmp_path):
    root = tmp_path / "vault"
    for sub in ("02-PROJECTS/archive", "99-INDEX", "99-SECRETS"):
        (root / sub).mkdir(parents=True)
    (root / "00-START-HERE.md").write_text("# Start\n\nvai su [[a]]\n", encoding="utf-8")
    (root / "02-PROJECTS" / "a.md").write_text(
        "# A\n\n[[b]] e [[missing-note]] e [[99-SECRETS/token-store]]\n", encoding="utf-8"
    )
    (root / "02-PROJECTS" / "b.md").write_text("# B\n\ntesto\n\n## Corpo\n\n[[a]]\n", encoding="utf-8")
    (root / "02-PROJECTS" / "orphan.md").write_text(
        "# Orphan\n\nsolo testo\n\n```\n[[fenced-link]]\n```\n", encoding="utf-8"
    )
    (root / "02-PROJECTS" / "archive" / "parked.md").write_text(
        "# Parked\n\n[[gone-note]]\n", encoding="utf-8"
    )
    (root / "99-INDEX" / "note-index.md").write_text(
        "# Note Index\n\n- [[02-PROJECTS/orphan]]\n- [[02-PROJECTS/a]]\n", encoding="utf-8"
    )
    (root / "99-SECRETS" / "token-store.md").write_text("# secret\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return SimpleNamespace(root=root, git_dir=root / ".git", service=VaultService(make_settings(root, root / ".git")))


# --- map_overview: the probe-first compass ------------------------------------


def test_map_overview_counts_and_semantics(vault):
    data = vault.service.map_overview()
    assert data["notes"] >= 5
    broken = {(entry["source"], entry["target"]) for entry in data["broken"]}
    assert ("02-PROJECTS/a.md", "missing-note") in broken
    # 99-SECRETS target exists on disk: valid-but-excluded, never broken.
    assert all("99-SECRETS" not in entry["target"] for entry in data["broken"])
    # Archive-sourced dead links are frozen history, counted separately.
    assert data["archived_broken_count"] == 1
    assert all("archive" not in Path(entry["source"]).parts for entry in data["broken"])
    # The generated index links orphan.md, but that never rescues an orphan;
    # the fenced [[fenced-link]] inside it is quotation, not linkage.
    assert "02-PROJECTS/orphan.md" in data["orphans"]
    assert all(entry["target"] != "fenced-link" for entry in data["broken"])


def test_map_overview_hubs_exclude_generated_index_and_are_bounded(vault):
    data = vault.service.map_overview()
    inbound = {entry["path"]: entry["inbound"] for entry in data["hubs"]}
    # a.md: START-HERE (structural) + b.md body (structural) + note-index
    # (generated, excluded) = 2.
    assert inbound["02-PROJECTS/a.md"] == 2
    assert len(data["hubs"]) <= 10
    assert len(data["broken"]) <= 15
    assert len(data["orphans"]) <= 15


def test_map_overview_works_on_a_read_only_instance(vault):
    readonly = VaultService(make_settings(vault.root, None, write_enabled=False))
    data = readonly.map_overview()
    assert data["notes"] >= 5


# --- write-time advisory: discipline, never a gate ----------------------------


def test_create_with_dead_link_warns_but_commits(vault):
    result = vault.service.create_note(
        "02-PROJECTS/new.md", "# New\n\nvedi [[ghost-note]] e [[a]]\n"
    )
    assert result["committed"] is True, "the advisory must NEVER block the write"
    assert [entry["target"] for entry in result["unresolved_links"]] == ["ghost-note"]


def test_clean_write_reports_no_unresolved_links(vault):
    result = vault.service.create_note("02-PROJECTS/clean.md", "# Clean\n\nvedi [[a]]\n")
    assert result["unresolved_links"] == []


def test_advisory_ignores_fenced_quotes_secrets_and_forward_hint(vault):
    result = vault.service.create_note(
        "02-PROJECTS/quoting.md",
        "# Quoting\n\n[[99-SECRETS/token-store]] e [[02-PROJECTS/parked]]\n\n"
        "```\n[[dead-in-fence]]\n```\n",
    )
    targets = {entry["target"]: entry for entry in result["unresolved_links"]}
    assert "dead-in-fence" not in targets, "fenced code is quotation"
    assert "99-SECRETS/token-store" not in targets, "existing excluded target is valid"
    # A path-qualified link to a note that moved into archive/ gets a hint.
    assert targets["02-PROJECTS/parked"]["hint"] == "02-PROJECTS/archive/parked.md"


def test_update_section_advisory_covers_only_the_new_span(vault):
    note = vault.service.read_note("02-PROJECTS/b.md")
    hashes = {s["heading"]: s["content_hash"] for s in note["sections"]}
    result = vault.service.update_section(
        "02-PROJECTS/b.md",
        "## Corpo",
        "## Corpo\n\n[[a]] e [[brand-new-target]]\n",
        hashes["## Corpo"],
    )
    assert result["committed"] is True
    assert [entry["target"] for entry in result["unresolved_links"]] == ["brand-new-target"]


def test_append_advisory_covers_only_the_appended_chunk(vault):
    # a.md already contains [[missing-note]]; the append itself is clean, so
    # the advisory must stay empty (it audits the delta, not the whole note).
    result = vault.service.append_note("02-PROJECTS/a.md", "\naggiunta pulita [[b]]\n")
    assert result["committed"] is True
    assert result["unresolved_links"] == []
