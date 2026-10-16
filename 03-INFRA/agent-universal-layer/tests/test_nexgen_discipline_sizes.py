from discipline_repo import repo  # noqa: F401  (pytest fixture)
from nexgen_core.discipline import gates
from nexgen_core.discipline.config import Config
from nexgen_core.discipline.gitutil import changes

BIG = "x = 1\n" * 600


def run(repo, base):
    return gates.static_checks(changes(repo.path, base), Config())


def test_new_oversized_file_fails(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("big.py", BIG)
    assert any("600 lines" in str(v) for v in run(repo, base))


def test_existing_oversized_file_may_exist_but_not_grow(repo):
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.write("big.py", BIG + "y = 2\n")
    assert any("grew from 600" in str(v) for v in run(repo, base))
    repo.write("big.py", BIG)
    assert run(repo, base) == []


def test_shrinking_an_oversized_file_passes(repo):
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.write("big.py", "x = 1\n" * 590)
    assert run(repo, base) == []


def test_long_function_fails_only_when_new_or_longer(repo):
    body = "def f():\n" + "    pass\n" * 70
    base = repo.git("rev-parse", "HEAD")
    repo.write("a.py", body)
    assert any("function f is 71" in str(v) for v in run(repo, base))
    base = repo.commit("legacy")
    repo.write("a.py", body + "\n\nx = 1\n")
    assert run(repo, base) == []
    repo.write("a.py", body + "    pass\n")
    assert run(repo, base)


def test_excluded_paths_are_ignored(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("vendor/big.py", BIG)
    assert run(repo, base) == []
