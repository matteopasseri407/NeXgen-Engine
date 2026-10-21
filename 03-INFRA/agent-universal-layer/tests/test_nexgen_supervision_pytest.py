"""Il conteggio dei test raccolti e l'ambiente in cui gira: le due parti che l'AST non vede.

Qui si prova il modulo da solo: quali cartelle si contano, cosa l'ambiente ripulito
toglie, come si legge il numero che pytest stampa, e che l'albero contato non sia
un repository. I casi d'attacco completi passano dal cancello, in
`test_nexgen_supervision_gates.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

from nexgen_local import supervision_pytest as pytest_runs

from supervision_repo import build, git_in


def _change(path: str, old_path: str | None = None, new: str = "", old: str = "") -> SimpleNamespace:
    return SimpleNamespace(path=path, old_path=old_path, new=new, old=old)


def test_the_cleaned_environment_drops_what_a_unit_could_fill(monkeypatch):
    for key in pytest_runs.UNTRUSTED_ENV:
        monkeypatch.setenv(key, "valore")
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)

    environment = pytest_runs.cleaned_environment()

    assert all(key not in environment for key in pytest_runs.UNTRUSTED_ENV)
    # The user site is outside the copy: forcing it off broke a project's own tests.
    assert "PYTHONNOUSERSITE" not in environment


def test_the_touched_tests_name_the_test_folder_that_holds_them():
    changes = [_change("03-INFRA/agent-universal-layer/tests/test_uno.py")]

    assert pytest_runs.paths_to_collect(changes, []) == (False, ["03-INFRA/agent-universal-layer/tests"])


def test_a_test_outside_a_test_folder_is_counted_by_its_own_path():
    changes = [_change("src/test_uno.py")]

    assert pytest_runs.paths_to_collect(changes, []) == (False, ["src/test_uno.py"])


def test_a_test_moved_into_a_hidden_folder_is_counted_where_it_came_from():
    """La cartella viene dal nome della test folder, non dal padre del file: `.hidden` non conta come radice."""
    moved = _change("tests/.hidden/test_uno.py", "tests/test_uno.py")

    assert pytest_runs.paths_to_collect([moved], []) == (False, ["tests"])


def test_a_touched_conftest_means_the_whole_suite():
    """Un conftest o un file di configurazione cambia come si raccoglie ogni cartella."""
    conftest = _change("tests/conftest.py")

    assert pytest_runs.paths_to_collect([conftest], ["tests/conftest.py"]) == (True, [])
    assert pytest_runs.paths_to_collect([_change("pytest.ini")], ["pytest.ini"]) == (True, [])


def test_a_conftest_or_a_pytest_config_is_the_pytest_surface():
    """I file che cambiano come si raccoglie finiscono nel pacchetto, con il loro nome."""
    assert pytest_runs.surface([_change("tests/conftest.py")]) == ["tests/conftest.py"]
    assert pytest_runs.surface([_change("pytest.ini")]) == ["pytest.ini"]
    assert pytest_runs.surface([_change("pyproject.toml", new="[tool.pytest.ini_options]\n")]) == ["pyproject.toml"]
    assert pytest_runs.surface([_change("pyproject.toml", new="[project]\nname = 'x'\n")]) == []
    assert pytest_runs.surface([_change("module.py")]) == []


def test_a_unit_that_touches_no_test_has_nothing_to_compare(tmp_path):
    assert pytest_runs.check(tmp_path, "", "", [_change("docs/guida.md")], []) == (
        True, "nessun test né configurazione di pytest toccato: niente da confrontare",
    )


def test_every_exported_tree_carries_no_git_directory(tmp_path):
    """La copia contata non è un repository: il codice importato non ha un remoto verso cui spingere."""
    repo = build(tmp_path / "sandbox")
    copy = tmp_path / "copia"

    assert pytest_runs._export(repo.path, repo.head(), copy, 60.0) == ""

    assert (copy / "module.py").is_file()
    assert not (copy / ".git").exists()
    assert git_in(copy, "rev-parse", "--show-toplevel", check=False).returncode != 0


def test_the_number_is_the_tests_listed_and_the_summary_must_agree():
    assert pytest_runs._count("tests/test_x.py::test_one\n\n1 test collected in 0.01s\n") == 1
    assert pytest_runs._count("tests/test_x.py::test_one\n\nno tests collected in 0.01s\n") is None
    assert pytest_runs._count("999 tests collected in 0.01s\n") is None
    assert pytest_runs._count("") is None


def test_a_full_suite_counts_the_base_and_the_head(tmp_path):
    """Il conteggio vero, su un repository vero: due test nella base, tre con un test in più."""
    repo = build(tmp_path / "sandbox")
    base = repo.head()
    repo.write("tests/test_nuovo.py", "def test_nuovo():\n    assert True\n")
    head = repo.commit("test: aggiunge un test")

    before, after = pytest_runs.counts(repo.path, base, head, dirs=["tests"])

    assert (before.ok, before.tests) == (True, 2)
    assert (after.ok, after.tests) == (True, 3)
    assert pytest_runs.check(repo.path, base, head, [_change("tests/test_nuovo.py")], [])[0] is True
