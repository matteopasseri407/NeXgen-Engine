"""The size ceiling is an alarm and a work list, never a gate.

What is over the ceiling is printed by `check`/`gate` as a warning and listed by
`report`/`audit` as the split queue. The gates that still refuse work are the
shell/PowerShell one, the test-integrity one and the swallowed-error one.
"""
from nexgen_core.discipline import gates
from nexgen_core.discipline.config import Config
from nexgen_core.discipline.gitutil import changes
from nexgen_core.discipline.sizes import split_queue

BIG = "x = 1\n" * 600


def run(repo, base):
    return gates.static_checks(changes(repo.path, base), Config())


def alarm(repo, base):
    return gates.size_alarm(changes(repo.path, base), Config())


def test_an_oversized_file_is_an_alarm_and_never_a_violation(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("big.py", BIG)
    assert run(repo, base) == []
    assert any("600 lines" in str(v) for v in alarm(repo, base))


def test_the_alarm_says_which_way_the_file_moved(repo):
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.write("big.py", BIG + "y = 2\n")
    assert any("601 lines" in str(v) and "grew from 600" in str(v) for v in alarm(repo, base))
    repo.write("big.py", "x = 1\n" * 590)
    assert any("590 lines" in str(v) and "shrank from 600" in str(v) for v in alarm(repo, base))
    assert run(repo, base) == []


def test_the_split_queue_lists_what_is_over_the_ceiling(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("big.py", BIG)
    repo.write("small.py", "x = 1\n")
    queued = [str(v) for v in split_queue(changes(repo.path, base), Config())]
    assert any("big.py" in line for line in queued)
    assert not any("small.py" in line for line in queued)


def test_a_file_that_declares_its_single_job_is_not_queued_until_the_hard_cap(repo):
    base = repo.git("rev-parse", "HEAD")
    job = '# nexgen: one-job reason="the Italian message table, one entry per sentence"\n'
    repo.write("table.py", job + "X = 1\n" * 700)
    assert split_queue(changes(repo.path, base), Config()) == []
    repo.write("table.py", job + "X = 1\n" * 1100)
    assert any("1101 lines" in str(v) for v in split_queue(changes(repo.path, base), Config()))


def test_a_release_prints_no_size_alarm_its_pushes_already_showed(repo):
    """A release diff spans every push since the last release: printing the whole
    accumulated queue there would only repeat what each push already said."""
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.write("big.py", BIG + "y = 2\n")
    repo.write("deploy.sh", "echo step\n" * 60)
    release = gates.run(repo.path, Config(), base, None, False, release=True)
    assert release.warnings == []
    assert any("deploy.sh" in str(v) for v in release.violations)
    push = gates.run(repo.path, Config(), base, None, False)
    assert any("big.py" in str(v) for v in push.warnings)


def test_a_long_function_is_queued_and_does_not_block(repo):
    body = "def f():\n" + "    pass\n" * 70
    base = repo.git("rev-parse", "HEAD")
    repo.write("a.py", body)
    assert any("function f is 71" in str(v) for v in alarm(repo, base))
    assert run(repo, base) == []


def test_excluded_paths_are_ignored(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("vendor/big.py", BIG)
    assert split_queue(changes(repo.path, base), Config()) == []
