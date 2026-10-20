"""I cancelli del motore su un ramo di unità: verdetti, motivi e pacchetto.

Repository git veri, rami veri, un remoto vero e nudo. La verifica la esegue il
motore con un interprete esplicito, così il test non dipende da quale `python`
sta su PATH.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexgen_local import supervision_gates as gates
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in

VERIFY_OK = f'"{sys.executable}" -c "print(1)"'
GATE_NAMES = (
    "confine",
    "file vietati",
    "integrità dei test",
    "prova per commit",
    "verifica indipendente",
    "remoto intatto",
    "albero pulito",
)


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova fase 2")["id"]


def _unit(task, repo, tmp_path, *, allow=("docs/*.md",), verify=VERIFY_OK) -> Path:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief dell'unità prova\n\nSolo fatti.\n", encoding="utf-8")
    record = worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=list(allow), verify=verify,
    )
    return Path(record["worktree"])


def _write(folder: Path, name: str, text: str) -> None:
    target = folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(folder: Path, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        _write(folder, name, text)
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", message)


def _gate(result: dict, name: str) -> gates.GateResult:
    return next(item for item in result["gates"] if item.name == name)


def test_a_clean_unit_inside_its_boundary_passes(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nseconda riga aggiunta.\n"})

    result = gates.collect(task, "prova")

    assert result["verdict"] == "PASS"
    assert [item.name for item in result["gates"]] == list(GATE_NAMES)
    assert all(item.ok for item in result["gates"]), [str(item) for item in result["gates"]]
    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "verdetto: PASS" in package
    assert "+seconda riga aggiunta." in package
    assert "docs/guida.md" in package
    assert "nessuna sessione agganciata" in package
    assert "## Test saltati su questa piattaforma" in package
    assert "nessun test saltato fra i file toccati" in package
    raw = Path(result["raw"]).read_text(encoding="utf-8")
    assert "PASS confine" in raw
    collected = [row for row in state.read_events(task) if row.get("event") == "unit_collected"]
    assert collected[-1]["verdict"] == "PASS"


def test_a_file_outside_the_boundary_fails_and_is_named(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "feat: tocca altro", {"src/altro.py": "x = 1\n"})

    result = gates.collect(task, "prova")

    assert result["verdict"] == "FAIL"
    gate = _gate(result, "confine")
    assert not gate.ok
    assert "src/altro.py" in gate.reason


def test_touching_version_fails(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "chore: alza la versione", {"VERSION": "2.99.0\n"})

    result = gates.collect(task, "prova")

    forbidden = _gate(result, "file vietati")
    assert not forbidden.ok
    assert "VERSION" in forbidden.reason
    assert not _gate(result, "confine").ok, "VERSION non è nel confine dichiarato"
    assert result["verdict"] == "FAIL"


def test_a_forbidden_file_declared_in_the_boundary_is_allowed(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["docs/*.md", "VERSION"])
    _commit(folder, "chore: alza la versione", {"VERSION": "2.99.0\n"})

    result = gates.collect(task, "prova")

    assert _gate(result, "file vietati").ok, "il confine lo dichiara, quindi non è un furto"


def test_a_renamed_version_file_fails_and_is_named_old_and_new(task, repo, tmp_path):
    """`git mv VERSION altro` è una modifica a VERSION: guardare solo il nome nuovo la nasconde."""
    repo.write("VERSION", "2.16.7\n")
    repo.commit("chore: la base ha un VERSION")
    repo.git("push", "-q", "origin", "main")
    folder = _unit(task, repo, tmp_path)
    git_in(folder, "mv", "VERSION", "versione-nascosta.txt")
    git_in(folder, "commit", "-q", "-m", "chore: sposta la versione")

    result = gates.collect(task, "prova")

    forbidden = _gate(result, "file vietati")
    assert not forbidden.ok
    assert "VERSION" in forbidden.reason
    assert result["verdict"] == "FAIL"


def test_a_weakened_test_fails_even_when_the_verification_is_green(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "test: toglie un test", {
        "tests/test_sample.py": "from module import value\n\ndef test_value():\n    assert value() == 1\n",
    })

    result = gates.collect(task, "prova")

    assert _gate(result, "verifica indipendente").ok, "la verifica dichiarata è verde"
    integrity = _gate(result, "integrità dei test")
    assert not integrity.ok
    assert "weakened" in integrity.reason
    assert result["verdict"] == "FAIL"


def test_a_fix_commit_without_a_failing_test_fails(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "fix: cambia il valore", {
        "module.py": "def value():\n    return 2\n",
        "tests/test_nuovo.py": "def test_nuovo():\n    assert 1 in (1, 2)\n",
    })

    result = gates.collect(task, "prova")

    per_commit = _gate(result, "prova per commit")
    assert not per_commit.ok
    assert "pass on the code before the fix" in per_commit.reason
    assert result["verdict"] == "FAIL"


def test_uncommitted_work_fails(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _write(folder, "docs/guida.md", "# guida\n\nsporca\n")

    result = gates.collect(task, "prova")

    clean = _gate(result, "albero pulito")
    assert not clean.ok
    assert "docs/guida.md" in clean.reason
    assert result["verdict"] == "FAIL"


def test_a_remote_that_advanced_for_another_reason_passes(task, repo, tmp_path):
    """Un ramo remoto che si muove per conto suo non è la prova di una spinta dell'unità."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    repo.git("push", "-q", "origin", "main:refs/heads/da-fuori")

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert remote.ok, remote.reason
    assert "nessun ref remoto porta un commit dell'unità" in remote.reason
    assert result["verdict"] == "PASS"


def test_a_remote_removed_from_the_copy_fails_and_is_named(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "remote", "remove", "origin")

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "remoti spariti" in remote.reason
    assert "origin" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_remote_whose_url_changed_fails_and_names_it(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "remote", "set-url", "origin", str(tmp_path / "altro.git"))

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "URL di fetch cambiato" in remote.reason
    assert "origin" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_unit_commit_that_reached_a_remote_fails_and_names_the_ref(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "push", "-q", str(repo.bare), "HEAD:refs/heads/fuga")

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "origin/fuga" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_failing_verification_fails_and_keeps_its_output(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, verify=f'"{sys.executable}" -c "import sys; sys.exit(3)"')
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    verify = _gate(result, "verifica indipendente")
    assert not verify.ok
    assert "esce con 3" in verify.reason
    assert result["verdict"] == "FAIL"


def test_a_unit_without_a_verification_command_is_not_verified(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, verify="")
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    verify = _gate(result, "verifica indipendente")
    assert not verify.ok
    assert "nessun comando di verifica" in verify.reason


def test_the_package_carries_the_cost_measured_at_collection(task, repo, tmp_path, lane, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    state.append_jsonl(
        lane / task / state.COSTS_FILE,
        {"unit": "prova", "role": "esecutore", "session": "ses_uno", "cost_usd": 0.0,
         "tokens_in": 0, "tokens_out": 0},
    )
    monkeypatch.setattr(
        gates, "session_facts",
        lambda session: gates.costs.SessionFacts(
            cost_usd=0.25, tokens_in=100, tokens_out=10,
            created_ms=1_000_000, updated_ms=1_060_000,
        ),
    )

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "0.2500 $ (alla raccolta, 0.0000 $ all'aggancio)" in package
    assert "ses_uno" in package
    assert "60.0s" in package


def test_a_cost_that_cannot_be_measured_at_collection_is_not_zero(task, repo, tmp_path, lane, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    state.append_jsonl(
        lane / task / state.COSTS_FILE,
        {"unit": "prova", "role": "esecutore", "session": "ses_uno", "cost_usd": 0.0},
    )

    def failing(session):
        raise gates.costs.CostError("l'export non risponde")

    monkeypatch.setattr(gates, "session_facts", failing)

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "non misurato alla raccolta (l'export non risponde)" in package
    assert "0.0000 $" not in package


def test_the_diff_in_the_package_is_cut_with_the_cut_declared(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    body = "\n".join(f"riga {number}" for number in range(gates.DIFF_MAX_LINES + 50))
    _commit(folder, "docs: un file lungo", {"docs/lungo.md": body + "\n"})

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "Diff troncato:" in package
    assert f"tetto dichiarato: {gates.DIFF_MAX_LINES} righe" in package


def test_an_unknown_unit_is_refused(task, repo, tmp_path):
    with pytest.raises(state.SupervisionError, match="unità sconosciuta"):
        gates.collect(task, "mai-creata")


def test_a_test_skipped_on_this_platform_is_named_in_the_package(task, repo, tmp_path):
    """Un test che qui viene saltato non è verificato: il pacchetto lo dice, con il motivo."""
    repo.write(
        "tests/test_posix.py",
        "import sys\nimport pytest\n\n"
        "@pytest.mark.skipif(sys.platform != 'un-altra-macchina', reason='solo sull\\'altra macchina')\n"
        "def test_solo_altrove():\n    assert False\n",
    )
    repo.commit("test: aggiunge un test che gira solo altrove")
    repo.git("push", "-q", "origin", "main")
    folder = _unit(task, repo, tmp_path, allow=["tests/*.py"])
    _commit(folder, "test: tocca il test saltato", {"tests/test_posix.py": (
        "import sys\nimport pytest\n\n"
        "@pytest.mark.skipif(sys.platform != 'un-altra-macchina', reason='solo sull\\'altra macchina')\n"
        "def test_solo_altrove():\n    assert False\n\n"
        "def test_aggiunto():\n    assert True\n"
    )})

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "## Test saltati su questa piattaforma" in package
    assert "tests/test_posix.py:4 (solo sull'altra macchina)" in package
    assert "non verificato su" in package
    raw = Path(result["raw"]).read_text(encoding="utf-8")
    assert "SKIPPED su questa piattaforma" in raw
    collected = [row for row in state.read_events(task) if row.get("event") == "unit_collected"]
    assert collected[-1]["skipped_tests"], "l'elenco resta anche nel diario"


def test_a_repository_whose_rules_do_not_parse_is_refused_with_a_reason(task, repo, tmp_path):
    repo.write(".nexgen-discipline.toml", "review_paths = [unclosed\n")
    repo.commit("chore: rompe la configurazione")
    repo.git("push", "-q", "origin", "main")
    _ = _unit(task, repo, tmp_path)

    with pytest.raises(state.SupervisionError, match="cancelli non eseguibili"):
        gates.collect(task, "prova")


def test_a_vanished_copy_is_refused(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    git_in(repo.path, "worktree", "remove", "--force", str(folder))

    with pytest.raises(state.SupervisionError, match="copia dell'unità sparita"):
        gates.collect(task, "prova")
