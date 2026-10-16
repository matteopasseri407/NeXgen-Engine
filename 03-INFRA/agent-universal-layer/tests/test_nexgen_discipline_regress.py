from nexgen_core.discipline.config import Config
from nexgen_core.discipline.regress import check_commits

CFG = Config(review_paths=("src/*",))
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
REAL_TEST = "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
TAUTOLOGY = "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == add(2, 3)\n"


def start(repo, on_main=False):
    repo.write("src/__init__.py", "")
    repo.write("src/calc.py", BUGGY)
    base = repo.commit("add calc")
    repo.git("update-ref", "refs/remotes/origin/main", base)  # what the remote already has
    if not on_main:
        repo.git("checkout", "-q", "-b", "developer")
    return base


def problems(repo, base):
    return [str(v) for v in check_commits(repo.path, CFG, base, "HEAD")]


def test_fix_with_a_test_that_fails_before_passes_the_gate(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.write("tests/test_calc.py", REAL_TEST)
    repo.commit("fix: add subtracted instead of adding")
    assert problems(repo, base) == []


def test_fix_whose_test_passes_before_the_fix_is_refused(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.write("tests/test_calc.py", TAUTOLOGY)
    repo.commit("fix: add subtracted instead of adding")
    assert any("prove nothing" in p for p in problems(repo, base))


def test_fix_without_any_test_is_refused(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.commit("fix(calc): add subtracted instead of adding")
    assert any("a fix needs a test" in p for p in problems(repo, base))


def test_critical_change_without_a_test_is_refused_whatever_its_title(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.commit("tidy the maths")
    assert any("critical code changed" in p for p in problems(repo, base))


def test_a_reasoned_no_test_marker_in_the_message_is_accepted(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.commit('fix: add\n\nnexgen: no-test reason="the failing case needs real hardware"')
    assert problems(repo, base) == []


def test_a_short_reason_does_not_count(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.commit('tidy\n\nnexgen: no-test reason="meh"')
    assert problems(repo, base)


def test_non_critical_change_needs_no_test(repo):
    base = start(repo)
    repo.write("docs/notes.md", "hello\n")
    repo.write("tools/helper.py", "X = 1\n")
    repo.commit("add notes")
    assert problems(repo, base) == []


def test_commits_already_published_are_not_judged(repo):
    start(repo)
    repo.write("src/calc.py", FIXED)
    published = repo.commit("tidy the maths")
    repo.git("update-ref", "refs/remotes/origin/main", published)
    assert problems(repo, repo.git("rev-parse", "HEAD~1")) == []


def test_a_project_working_on_main_is_judged_too(repo):
    base = start(repo, on_main=True)
    repo.write("src/calc.py", FIXED)
    repo.write("tests/test_calc.py", TAUTOLOGY)
    repo.commit("fix: add subtracted instead of adding")
    assert any("prove nothing" in p for p in problems(repo, base))


def test_a_test_that_fails_even_after_the_fix_is_refused(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.write("tests/test_calc.py", "def test_never():\n    assert False\n")
    repo.commit("fix: add subtracted instead of adding")
    assert any("do not pass after the fix" in p for p in problems(repo, base))


def test_a_missing_test_runner_is_refused_not_trusted(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED)
    repo.write("tests/test_calc.py", TAUTOLOGY)
    repo.commit("fix: add subtracted instead of adding")
    cfg = Config(review_paths=("src/*",), test_command="python -m no_such_test_runner")
    assert any("runner is missing" in str(v) for v in check_commits(repo.path, cfg, base, "HEAD"))


def test_a_fix_whose_test_imports_the_new_code_is_accepted(repo):
    base = start(repo)
    repo.write("src/calc.py", FIXED + "\n\ndef plus(a, b):\n    return a + b\n")
    repo.write("tests/test_calc.py", "from src.calc import plus\n\n\ndef test_plus():\n    assert plus(2, 3) == 5\n")
    repo.commit("fix: add subtracted instead of adding")
    assert problems(repo, base) == []


def test_a_fix_that_touches_no_code_needs_no_proof(repo):
    base = start(repo)
    repo.write("README.md", "typo fixed\n")
    repo.commit("fix(docs): typo")
    assert problems(repo, base) == []


def test_a_moved_critical_file_is_still_critical(repo):
    base = start(repo)
    repo.git("mv", "src/calc.py", "lib_calc.py")
    repo.commit("tidy: move the file")
    assert any("critical code changed" in p for p in problems(repo, base))
