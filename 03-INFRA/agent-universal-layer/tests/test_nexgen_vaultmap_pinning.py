"""Pinning del comportamento attuale di vault_map.scan e build_context.

Vault finto in tmp_path: fissa conteggi, broken+hint, orfani, hubs ordinati,
BFS per hops / max_nodes e FileNotFoundError per nota assente.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.vault_map import build_context, scan  # noqa: E402


def _make_pinning_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    files = {
        "00-START-HERE.md": "# Start\n\n[[hub]]\n",
        "hub.md": "# Hub\n\n[[leaf-a]]\n[[leaf-b]]\n",
        "leaf-a.md": "# Leaf A\n\n[[leaf-b]]\n[[deep]]\n",
        "leaf-b.md": "# Leaf B\n",
        "deep.md": "# Deep\n",
        "orphan.md": "# Orphan\n",
        "broken-holder.md": "# Broken Holder\n\n[[does-not-exist]]\n[[old-path/moved-target]]\n",
        "moved-target.md": "# Moved Target\n",
    }
    for name, text in files.items():
        (vault / name).write_text(text, encoding="utf-8")
    return vault


def test_scan_counts_notes_and_links(tmp_path: Path):
    data = scan(_make_pinning_vault(tmp_path))
    assert data["notes"] == 8
    assert data["links"] == 7


def test_scan_broken_with_moved_hint(tmp_path: Path):
    data = scan(_make_pinning_vault(tmp_path))
    assert len(data["broken"]) == 2
    plain = next(e for e in data["broken"] if e["target"] == "does-not-exist")
    assert plain["source"] == "broken-holder.md"
    assert "hint" not in plain
    moved = next(e for e in data["broken"] if e["target"] == "old-path/moved-target")
    assert moved["source"] == "broken-holder.md"
    assert moved["hint"] == "moved-target.md"


def test_scan_orphans(tmp_path: Path):
    data = scan(_make_pinning_vault(tmp_path))
    assert data["orphans"] == ["moved-target.md", "orphan.md"]


def test_scan_hubs_ordered(tmp_path: Path):
    data = scan(_make_pinning_vault(tmp_path))
    assert data["hubs"][0] == {"path": "leaf-b.md", "inbound": 2}
    rest = data["hubs"][1:]
    assert [(h["path"], h["inbound"]) for h in rest] == [
        ("deep.md", 1),
        ("hub.md", 1),
        ("leaf-a.md", 1),
    ]
    counts = [h["inbound"] for h in data["hubs"]]
    assert counts == sorted(counts, reverse=True)


def test_build_context_hops(tmp_path: Path):
    vault = _make_pinning_vault(tmp_path)
    hops0 = build_context(vault, "hub", hops=0, max_nodes=10)
    assert [n["path"] for n in hops0["nodes"]] == ["hub.md"]

    hops1 = build_context(vault, "hub", hops=1, max_nodes=10)
    paths1 = [n["path"] for n in hops1["nodes"]]
    assert paths1[0] == "hub.md"
    assert "leaf-a.md" in paths1 and "leaf-b.md" in paths1
    assert "00-START-HERE.md" in paths1
    assert "deep.md" not in paths1

    hops2 = build_context(vault, "hub", hops=2, max_nodes=10)
    paths2 = [n["path"] for n in hops2["nodes"]]
    assert "deep.md" in paths2
    deep = next(n for n in hops2["nodes"] if n["path"] == "deep.md")
    assert deep["depth"] == 2
    assert deep["via"] == "leaf-a.md"
    assert deep["direction"] == "out"


def test_build_context_max_nodes_caps(tmp_path: Path):
    vault = _make_pinning_vault(tmp_path)
    capped = build_context(vault, "hub", hops=2, max_nodes=2)
    assert len(capped["nodes"]) <= 3
    assert capped["nodes"][0]["path"] == "hub.md"
    assert capped["hops"] == 2
    assert capped["max_nodes"] == 2


def test_build_context_missing_note_raises(tmp_path: Path):
    vault = _make_pinning_vault(tmp_path)
    with pytest.raises(FileNotFoundError):
        build_context(vault, "inesistente", hops=1, max_nodes=10)
