import subprocess

from discipline_repo import repo  # noqa: F401  (pytest fixture)
from nexgen_core.discipline import cli
from nexgen_core.discipline.health import report, run_smoke
from nexgen_core.discipline.scaffold import init_project

TESTS = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n    assert 3\n"
BIG = "x = 1\n" * 600


def committed_tests(repo, text=TESTS, name="tests/test_x.py"):
    repo.write(name, text)
    return repo.commit("add tests")


def test_deleting_a_test_file_is_flagged(repo):
    base = committed_tests(repo)
    (repo.path / "tests/test_x.py").unlink()
    assert cli.main(["check", "--base", base]) == 1


def test_fewer_tests_or_assertions_or_a_new_skip_are_flagged(repo):
    base = committed_tests(repo)
    for weakened in ("def test_a():\n    assert 1\n", TESTS.replace("assert 3", "pass"),
                     "import pytest\n\n\n@pytest.mark.skip\n" + TESTS):
        repo.write("tests/test_x.py", weakened)
        assert cli.main(["check", "--base", base]) == 1, weakened


def test_adding_tests_is_never_flagged(repo):
    base = committed_tests(repo)
    repo.write("tests/test_x.py", TESTS + "\n\ndef test_c():\n    assert 4\n")
    assert cli.main(["check", "--base", base]) == 0


def test_the_owner_can_allow_a_test_removal_through_the_base_config(repo):
    repo.write(".nexgen-discipline.toml", 'test_removal_ok = ["tests/test_x.py"]\n')
    committed_tests(repo)
    base = repo.git("rev-parse", "HEAD")
    (repo.path / "tests/test_x.py").unlink()
    assert cli.main(["check", "--base", base]) == 0


def test_a_new_swallowed_exception_is_flagged_but_an_explained_one_is_not(repo):
    base = repo.git("rev-parse", "HEAD")
    swallow = "try:\n    risky()\nexcept Exception:\n    pass\n"
    repo.write("mod.py", swallow)
    assert cli.main(["check", "--base", base]) == 1
    repo.write("mod.py", 'try:\n    risky()\nexcept Exception:  # nexgen: allow reason="optional cache"\n    pass\n')
    assert cli.main(["check", "--base", base]) == 0
    repo.write("mod.py", "try:\n    risky()\nexcept Exception as exc:\n    log(exc)\n")
    assert cli.main(["check", "--base", base]) == 0


def test_existing_swallowed_exceptions_do_not_block_other_edits(repo):
    repo.write("mod.py", "try:\n    risky()\nexcept Exception:\n    pass\n")
    base = repo.commit("legacy")
    repo.write("mod.py", "try:\n    risky()\nexcept Exception:\n    pass\n\nother = 1\n")
    assert cli.main(["check", "--base", base]) == 0


def test_init_creates_files_and_hooks_and_never_overwrites(repo):
    first = init_project(repo.path, "")
    assert (repo.path / "DECISIONS.md").exists() and (repo.path / ".nexgen-discipline.toml").exists()
    assert any("ATTENZIONE" in line for line in first)
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho mine\n")
    assert any("non lo tocco" in line for line in init_project(repo.path, ""))
    assert "mine" in hook.read_text()


def test_the_installed_hook_really_blocks_an_oversized_commit(repo):
    init_project(repo.path, "")
    repo.write("big.py", BIG)
    repo.git("add", "big.py")
    done = subprocess.run(["git", "-C", str(repo.path), "commit", "-q", "-m", "big"], capture_output=True, text=True)
    assert done.returncode != 0 and "big.py" in done.stdout + done.stderr


def test_the_hook_fails_loudly_when_the_engine_is_missing(repo):
    init_project(repo.path, "")
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text(hook.read_text().replace("discipline.py", "gone.py"))
    repo.write("a.txt", "a\n")
    repo.git("add", "a.txt")
    done = subprocess.run(["git", "-C", str(repo.path), "commit", "-q", "-m", "a"], capture_output=True, text=True)
    assert done.returncode != 0 and "engine not found" in done.stderr


def test_report_is_red_without_a_smoke_command_and_green_with_one(repo):
    assert "ROSSO" in report(repo.path) and "NON DEFINITO" in report(repo.path)
    repo.write(".nexgen-discipline.toml", 'smoke_command = "python3 -c pass"\n')
    repo.commit("define smoke")
    assert "VERDE" in report(repo.path)


def test_smoke_runs_the_project_command_and_reports_its_exit_code(repo):
    assert run_smoke(repo.path) == 0  # not defined: a notice, not a failure
    repo.write(".nexgen-discipline.toml", 'smoke_command = "python3 -c \\"raise SystemExit(3)\\""\n')
    repo.commit("failing smoke")
    assert run_smoke(repo.path) == 3
