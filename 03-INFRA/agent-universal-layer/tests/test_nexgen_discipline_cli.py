from nexgen_core.discipline import cli

BIG = "x = 1\n" * 600
SWALLOW = "try:\n    risky()\nexcept Exception:\n    pass\n"


def test_staged_check_warns_about_an_oversized_file_without_refusing_it(tmp_path, monkeypatch, capsys):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.py").write_text(BIG)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert cli.main(["check", "--staged"]) == 0
    out = capsys.readouterr().out
    assert "WARN" in out and "big.py" in out


def test_audit_reports_the_split_queue_without_failing(repo, capsys):
    repo.write("big.py", BIG)
    repo.commit("debt")
    assert cli.main(["audit"]) == 0
    assert "DEBT big.py" in capsys.readouterr().out


def test_moving_an_oversized_file_is_not_new_debt(repo):
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.git("mv", "big.py", "moved_big.py")
    assert cli.main(["check", "--base", base]) == 0


def test_the_ceiling_comes_from_the_base_not_from_the_change(repo, capsys):
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.write("big.py", BIG + "y = 2\n")
    repo.write(".nexgen-discipline.toml", "max_file_lines = 100000\n")
    assert cli.main(["check", "--base", base]) == 0
    assert "limit 500" in capsys.readouterr().out


def test_tests_may_exceed_the_file_limit_but_a_long_test_function_is_queued(repo, capsys):
    base = repo.git("rev-parse", "HEAD")
    repo.write("tests/test_big.py", BIG)
    assert cli.main(["check", "--base", base]) == 0
    assert "WARN" not in capsys.readouterr().out
    repo.write("tests/test_long.py", "def test_x():\n" + "    pass\n" * 70)
    assert cli.main(["check", "--base", base]) == 0
    assert "function test_x is 71" in capsys.readouterr().out


def test_ci_judges_the_first_push_of_a_branch_against_main(repo, capsys):
    repo.git("checkout", "-q", "-b", "side")
    repo.write("mod.py", SWALLOW)
    repo.commit("new")
    code = cli.main(["ci", "--event", "push", "--before", "0" * 40, "--head-ref", "side"])
    assert code == 1
    assert "swallow" in capsys.readouterr().out


def test_a_repository_adds_to_the_default_excludes_instead_of_replacing_them(repo):
    repo.write(".nexgen-discipline.toml", 'exclude = ["*/locales/*"]\n')
    base = repo.commit("config")
    repo.write("pkg/locales/it.py", BIG)
    repo.write("vendor/big.py", BIG)
    assert cli.main(["check", "--base", base]) == 0


def test_a_small_fix_to_an_oversized_reasoned_script_is_allowed(repo):
    script = '# nexgen: allow reason="deploy bootstrap that must run before python exists"\n' + "echo step\n" * 200
    repo.write("deploy.sh", script)
    base = repo.commit("legacy script")
    repo.write("deploy.sh", script + "echo one more fix\n")
    assert cli.main(["check", "--base", base]) == 0
    repo.write("deploy.sh", script + "echo fix\n" * 30)
    assert cli.main(["check", "--base", base]) == 1


def test_a_big_file_that_declares_its_one_job_is_not_queued_up_to_the_hard_cap(repo, capsys):
    base = repo.git("rev-parse", "HEAD")
    job = '# nexgen: one-job reason="the Italian message table, one entry per sentence"\n'
    repo.write("table.py", job + "X = 1\n" * 700)
    assert cli.main(["check", "--base", base]) == 0
    assert "WARN" not in capsys.readouterr().out
    repo.write("table.py", job + "X = 1\n" * 1100)
    assert cli.main(["check", "--base", base]) == 0
    assert "1101 lines" in capsys.readouterr().out


def test_a_one_job_note_needs_a_real_reason(repo, capsys):
    base = repo.git("rev-parse", "HEAD")
    repo.write("table.py", '# nexgen: one-job reason="ok"\n' + "X = 1\n" * 700)
    assert cli.main(["check", "--base", base]) == 0
    assert "701 lines" in capsys.readouterr().out
    repo.write("table.py", "# nexgen: one-job\n" + "X = 1\n" * 700)
    assert cli.main(["check", "--base", base]) == 0
    assert "701 lines" in capsys.readouterr().out


def test_one_job_does_not_excuse_a_long_function(repo, capsys):
    base = repo.git("rev-parse", "HEAD")
    repo.write("job.py", '# nexgen: one-job reason="a single parser for a single format"\n' + "def f():\n" + "    pass\n" * 70)
    assert cli.main(["check", "--base", base]) == 0
    assert "function f is 71" in capsys.readouterr().out
