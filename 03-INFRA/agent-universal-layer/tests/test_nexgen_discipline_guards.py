import subprocess

import sys
from pathlib import Path

from nexgen_core.discipline import cli
from nexgen_core.discipline.config import Config, load
from nexgen_core.discipline.gitutil import changes
from nexgen_core.discipline.health import report, run_smoke
from nexgen_core.discipline.integrity import check_test_integrity
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


def test_a_new_platform_only_test_is_not_a_weakening(repo):
    """Una prova nuova che gira solo su Windows nasce così: marcarla non indebolisce nulla.

    Il conteggio dei marcatori era un totale sul file, quindi il marcatore di un
    test appena scritto veniva letto come un test esistente spento.
    """
    base = committed_tests(repo)
    repo.write(
        "tests/test_x.py",
        TESTS
        + "\n\nimport os\nimport pytest\n\n\n"
        + '@pytest.mark.skipif(os.name != "nt", reason="a Windows-only reproduction")\n'
        + "def test_c():\n    assert 4\n",
    )
    assert cli.main(["check", "--base", base]) == 0


def test_a_new_test_that_skips_itself_is_not_a_weakening(repo):
    base = committed_tests(repo)
    repo.write(
        "tests/test_x.py",
        TESTS + "\n\nimport pytest\n\n\ndef test_c():\n    pytest.skip('only where the driver exists')\n    assert 4\n",
    )
    assert cli.main(["check", "--base", base]) == 0


def test_skipping_a_test_the_base_already_had_is_still_flagged(repo):
    base = committed_tests(repo)
    repo.write(
        "tests/test_x.py",
        "import os\nimport pytest\n\n\n"
        + '@pytest.mark.skipif(os.name != "nt", reason="qui la prova non gira")\n'
        + "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n    assert 3\n",
    )
    assert cli.main(["check", "--base", base]) == 1


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
    first, protected = init_project(repo.path, "")
    assert protected
    assert (repo.path / "DECISIONS.md").exists() and (repo.path / ".nexgen-discipline.toml").exists()
    assert any("ATTENZIONE" in line for line in first)
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho mine\n")
    lines, protected = init_project(repo.path, "")
    assert not protected and any("NON installato" in line for line in lines)
    assert "mine" in hook.read_text()


def test_the_installed_hooks_do_not_hand_gits_own_context_downstream(repo):
    """git exports GIT_DIR when it runs a hook, and a push from a linked
    worktree points it at that worktree. On 2026-10-16 a proof run started
    from the pre-push hook used it: tests edited the real repository and a
    test clone's push launched the same hook again, in a loop."""
    from nexgen_core.discipline.scaffold import HOOK_COMMANDS

    init_project(repo.path, "")
    for name in HOOK_COMMANDS:
        text = (repo.path / ".git" / "hooks" / name).read_text(encoding="utf-8")
        assert "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE" in text


def test_the_installed_hook_lets_an_oversized_commit_through_and_says_so(repo):
    init_project(repo.path, "")
    repo.write("big.py", BIG)
    repo.git("add", "big.py")
    done = subprocess.run(["git", "-C", str(repo.path), "commit", "-q", "-m", "big"], capture_output=True, text=True)
    assert done.returncode == 0
    assert "big.py" in done.stdout + done.stderr  # the alarm is shown; the commit is not refused


def test_the_hook_fails_loudly_when_the_engine_is_missing(repo):
    init_project(repo.path, "")
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text(hook.read_text().replace("discipline.py", "gone.py"))
    repo.write("a.txt", "a\n")
    repo.git("add", "a.txt")
    done = subprocess.run(["git", "-C", str(repo.path), "commit", "-q", "-m", "a"], capture_output=True, text=True)
    assert done.returncode != 0 and "not found" in done.stderr


def test_report_is_red_without_a_smoke_command_and_green_with_history(repo, monkeypatch):
    assert "ROSSO" in report(repo.path) and "NON DEFINITO" in report(repo.path)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-01T00:00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-01T00:00:00")
    repo.write(".nexgen-discipline.toml", 'smoke_command = "python3 -c pass"\n')
    repo.commit("define smoke, ten days ago")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    repo.write("a.txt", "a\n")
    repo.commit("recent")
    assert "VERDE" in report(repo.path)


def test_the_report_prints_the_split_queue(repo):
    """The ceiling is a warning: what is over it is work to do, listed for the owner."""
    repo.write("big.py", BIG)
    repo.write("small.py", "x = 1\n")
    repo.commit("debt")
    text = report(repo.path)
    assert "Coda split" in text and "big.py" in text
    assert "small.py" not in text


def test_smoke_runs_the_project_command_and_reports_its_exit_code(repo):
    assert run_smoke(repo.path) == 0  # not defined: a notice, not a failure
    repo.write(".nexgen-discipline.toml", 'smoke_command = "python3 -c \\"raise SystemExit(3)\\""\n')
    repo.commit("failing smoke")
    assert run_smoke(repo.path) == 3


def test_a_smoke_command_with_quotes_and_backslashes_keeps_the_config_valid(repo):
    command = 'py tests\\smoke.py --msg "ciao"'
    init_project(repo.path, command)
    repo.commit("init")
    assert load(repo.path, "HEAD").smoke_command == command


def test_a_broken_config_gives_a_clear_message_not_a_traceback(repo, capsys):
    repo.write(".nexgen-discipline.toml", "smoke_command = py tests\\x\n")
    base = repo.commit("broken")
    repo.write("a.py", "a = 1\n")
    assert cli.main(["check", "--base", base]) == 1
    assert "not valid TOML" in capsys.readouterr().err


def test_splitting_or_merging_tests_is_not_weakening(repo):
    ten = "".join(f"def test_{i}():\n    assert {i}\n\n\n" for i in range(10))
    base = committed_tests(repo, ten, "tests/test_all.py")
    half = "".join(f"def test_{i}():\n    assert {i}\n\n\n" for i in range(5))
    rest = "".join(f"def test_{i}():\n    assert {i}\n\n\n" for i in range(5, 10))
    repo.write("tests/test_all.py", half)
    repo.write("tests/test_more.py", rest)
    assert cli.main(["check", "--base", base]) == 0
    repo.write("tests/test_all.py", "import pytest\n\n\n@pytest.mark.parametrize(\"i\", [0, 1, 2, 3, 4])\ndef test_i(i):\n    assert i >= 0\n")
    assert cli.main(["check", "--base", base]) == 0


def test_deleting_a_fixture_or_readme_under_tests_is_not_a_deleted_test(repo):
    repo.write("tests/data/old.csv", "a,b\n")
    repo.write("tests/README.md", "notes\n")
    base = repo.commit("fixtures")
    (repo.path / "tests/data/old.csv").unlink()
    (repo.path / "tests/README.md").unlink()
    assert cli.main(["check", "--base", base]) == 0


def test_an_iterator_skip_in_a_test_is_not_a_skipped_test(repo):
    base = committed_tests(repo, "def test_a():\n    assert 1\n")
    repo.write("tests/test_x.py", "def test_a():\n    assert list(range(5))[1:] == list(iter(range(5)).skip(1))\n    assert 1\n")
    assert cli.main(["check", "--base", base]) == 0


def test_init_refuses_when_a_global_hooks_path_is_set(repo):
    repo.git("config", "core.hooksPath", "/tmp/somewhere-shared")
    lines, protected = init_project(repo.path, "")
    assert not protected and any("core.hooksPath" in line for line in lines)
    assert not (Path("/tmp/somewhere-shared") / "pre-commit").exists()


def test_the_hook_pins_the_python_that_ran_init(repo):
    init_project(repo.path, "")
    assert sys.executable.replace("\\", "/") in (repo.path / ".git" / "hooks" / "pre-commit").read_text()


def test_a_smoke_command_that_cannot_start_gives_a_plain_failure(repo, capsys):
    repo.write(".nexgen-discipline.toml", 'smoke_command = "definitely-not-a-program-xyz"\n')
    repo.commit("smoke")
    assert run_smoke(repo.path) != 0


def test_a_new_project_report_does_not_claim_green(repo):
    repo.write(".nexgen-discipline.toml", 'smoke_command = "python3 -c pass"\n')
    repo.commit("smoke")
    assert "GIALLO" in report(repo.path) and "VERDE" not in report(repo.path)


def test_more_shapes_of_swallowing_are_caught(repo):
    base = repo.git("rev-parse", "HEAD")
    for body in ("continue", "return []", "return {}", 'return ""'):
        repo.write("mod.py", f"def f(xs):\n    for x in xs:\n        try:\n            g(x)\n        except Exception:\n            {body}\n")
        assert cli.main(["check", "--base", base]) == 1, body


def test_moving_assertions_into_a_helper_is_not_weakening(repo):
    base = committed_tests(repo, "def test_a():\n    assert 1\n    assert 2\n")
    repo.write("tests/test_x.py", "def check():\n    assert 1\n    assert 2\n\n\ndef test_a():\n    check()\n")
    assert cli.main(["check", "--base", base]) == 0


def test_parametrize_cases_in_a_named_constant_are_counted_both_ways(repo):
    three = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n\n\ndef test_c():\n    assert 3\n"
    base = committed_tests(repo, three)
    head = "import pytest\n\nCASES = {cases}\n\n\n@pytest.mark.parametrize(\"i\", CASES)\ndef test_i(i):\n    assert i\n"
    repo.write("tests/test_x.py", head.format(cases="[1, 2, 3]"))
    assert cli.main(["check", "--base", base]) == 0  # three cases kept: only the shape changed
    repo.write("tests/test_x.py", head.format(cases="[1]"))
    assert cli.main(["check", "--base", base]) == 1  # two of the three cases are gone


def test_deleting_js_tests_is_flagged_even_if_an_empty_one_is_added(repo):
    old = "".join(f"it('case {i}', () => {{ expect({i}).toBe({i}); }});\n" for i in range(30))
    repo.write("tests/a.test.js", old)
    base = repo.commit("js tests")
    (repo.path / "tests/a.test.js").unlink()
    repo.write("tests/b.test.js", "// nothing yet\n")
    assert cli.main(["check", "--base", base]) == 1


def test_init_in_a_non_ascii_smoke_keeps_the_config_valid_and_rerun_stays_quiet(repo):
    init_project(repo.path, "echo ✓")
    repo.commit("init")
    assert load(repo.path, "HEAD").smoke_command == "echo ✓"
    lines, _ = init_project(repo.path, "")
    assert not any("ATTENZIONE" in line for line in lines)


def test_renaming_a_test_to_a_non_test_name_with_fewer_tests_is_flagged(repo):
    """A test renamed to a non-test name and weakened must not escape integrity checks."""
    ten = "".join(f"def test_{i}():\n    assert {i}\n\n\n" for i in range(10))
    base = committed_tests(repo, ten, "tests/test_x.py")
    repo.git("mv", "tests/test_x.py", "disabled_x.py")
    seven = "".join(f"def test_{i}():\n    assert {i}\n\n\n" for i in range(7))
    repo.write("disabled_x.py", seven)
    ch = changes(repo.path, base)
    violations = check_test_integrity(ch, Config())
    assert violations and any("tests weakened" in v.message for v in violations)

