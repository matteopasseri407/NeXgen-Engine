from discipline_repo import repo  # noqa: F401  (pytest fixture)
from nexgen_core.discipline import cli

BIG = "x = 1\n" * 600


def test_staged_check_blocks_oversized_file_even_on_first_commit(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.py").write_text(BIG)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert cli.main(["check", "--staged"]) == 1


def test_audit_reports_debt_without_failing(repo, capsys):
    repo.write("big.py", BIG)
    repo.commit("debt")
    assert cli.main(["audit"]) == 0
    assert "DEBT big.py" in capsys.readouterr().out


def test_moving_an_oversized_file_is_not_new_debt(repo):
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.git("mv", "big.py", "moved_big.py")
    assert cli.main(["check", "--base", base]) == 0


def test_limits_come_from_the_base_not_from_the_change(repo):
    repo.write("big.py", BIG)
    base = repo.commit("legacy")
    repo.write("big.py", BIG + "y = 2\n")
    repo.write(".nexgen-discipline.toml", "max_file_lines = 100000\n")
    assert cli.main(["check", "--base", base]) == 1


def test_tests_may_exceed_the_file_limit_but_not_function_limit(repo, capsys):
    base = repo.git("rev-parse", "HEAD")
    repo.write("tests/test_big.py", BIG)
    assert cli.main(["check", "--base", base]) == 0
    repo.write("tests/test_long.py", "def test_x():\n" + "    pass\n" * 70)
    assert cli.main(["check", "--base", base]) == 1


def test_ci_judges_the_first_push_of_a_branch_against_main(repo, capsys):
    repo.git("checkout", "-q", "-b", "side")
    repo.write("big.py", BIG)
    repo.commit("new")
    code = cli.main(["ci", "--event", "push", "--before", "0" * 40, "--head-ref", "side"])
    assert code == 1 and "601" in capsys.readouterr().out or code == 1


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
