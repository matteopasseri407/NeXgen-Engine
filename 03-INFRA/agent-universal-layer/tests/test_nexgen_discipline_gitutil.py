"""Il livello git della disciplina: ogni comando regge i percorsi lunghi di Windows.

Il 2026-10-24 il cancello "prova per commit" di una unità vera si è fermato con
`fatal: failed to stat '<sha>:03-INFRA/.../test_....py': Filename too long`: git
controlla se un argomento è un file prima di risolverlo come revisione, e su
Windows quel controllo passa da MAX_PATH. La costante vive qui, una volta sola, e
ogni comando che esce da questo modulo la porta con sé.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexgen_core.discipline import gitutil


@pytest.fixture
def seen(monkeypatch) -> list[list[str]]:
    """Ogni argv che il livello git passa al sistema, senza avviare niente."""
    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        calls.append([str(part) for part in argv])
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(gitutil.subprocess, "run", fake_run)
    return calls


def _asks_for_long_paths(argv: list[str]) -> bool:
    return any(argv[index:index + 2] == ["-c", "core.longpaths=true"] for index in range(len(argv) - 1))


def test_every_command_of_the_layer_asks_for_long_paths(seen, tmp_path):
    """Una revisione con un percorso (`<rev>:<file>`) è un argomento, e va letta oltre MAX_PATH."""
    gitutil.git(tmp_path, "status", "--porcelain")
    gitutil.show(tmp_path, "HEAD:03-INFRA/un/percorso/lungo/test_qualcosa.py")
    gitutil.is_ancestor(tmp_path, "a" * 40, "b" * 40)

    assert len(seen) == 3, seen
    assert all(_asks_for_long_paths(argv) for argv in seen), seen


def test_the_supervision_modules_import_the_same_constant():
    """Una copia sola: i quattro moduli che scrivono l'albero non tengono la loro."""
    from nexgen_local import supervision_publish, supervision_rebase
    from nexgen_local import supervision_retire, supervision_worktree

    shared = gitutil.LONG_PATHS
    assert shared == ("-c", "core.longpaths=true")
    for module in (supervision_publish, supervision_rebase, supervision_retire, supervision_worktree):
        assert module.LONG_PATHS is shared, module.__name__
