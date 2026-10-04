"""Selected-file publication preserves work staged by another writer."""
import subprocess

from nexgen_core.git_ops import auto_commit_infra_files, publish_changes


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True).stdout


def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    for name in ("selected.txt", "other.txt"):
        (repo / name).write_text("baseline\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    return repo


def test_publish_explicit_files_commits_only_those_paths(tmp_path):
    repo = repository(tmp_path)
    (repo / "selected.txt").write_text("requested\n")
    (repo / "other.txt").write_text("another author\n")
    git(repo, "add", "other.txt")
    before = git(repo, "show", ":other.txt")
    ok, msg = publish_changes(repo, remote="local", commit_msg="selected", files_to_commit=["selected.txt"])
    assert ok, msg
    assert git(repo, "show", "--pretty=", "--name-only", "HEAD").splitlines() == ["selected.txt"]
    assert git(repo, "show", ":other.txt") == before
    assert git(repo, "diff", "--cached", "--name-only").splitlines() == ["other.txt"]


def test_unchanged_explicit_file_does_not_commit_another_authors_index(tmp_path):
    repo = repository(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    (repo / "other.txt").write_text("another author\n")
    git(repo, "add", "other.txt")
    ok, msg = publish_changes(repo, remote="local", commit_msg="selected", files_to_commit=["selected.txt"])
    assert ok, msg
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "diff", "--cached", "--name-only").splitlines() == ["other.txt"]


def test_auto_commit_infra_preserves_other_staged_work(tmp_path):
    repo = repository(tmp_path)
    infra = repo / "03-INFRA" / "settings.yaml"
    infra.parent.mkdir()
    infra.write_text("setting: baseline\n")
    git(repo, "add", "03-INFRA/settings.yaml")
    git(repo, "commit", "-m", "infra baseline")
    infra.write_text("setting: changed\n")
    (repo / "other.txt").write_text("another author\n")
    git(repo, "add", "other.txt")
    staged = git(repo, "show", ":other.txt")
    ok, paths = auto_commit_infra_files(repo)
    assert ok
    assert paths == ["03-INFRA/settings.yaml"]
    assert git(repo, "show", "--pretty=", "--name-only", "HEAD").splitlines() == paths
    assert git(repo, "show", ":other.txt") == staged
    assert git(repo, "diff", "--cached", "--name-only").splitlines() == ["other.txt"]


def test_remote_divergence_preserves_selected_commit_and_other_staged_work(tmp_path):
    repo = repository(tmp_path)
    remote = tmp_path / "remote.git"
    git(repo, "init", "--bare", "--initial-branch=main", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    peer = tmp_path / "peer"
    git(repo, "clone", str(remote), str(peer))
    git(peer, "config", "user.name", "Peer")
    git(peer, "config", "user.email", "peer@example.com")
    git(peer, "config", "commit.gpgsign", "false")
    git(peer, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    (peer / "remote.txt").write_text("concurrent commit\n")
    git(peer, "add", ".")
    git(peer, "commit", "-m", "peer")
    git(peer, "push", "origin", "main")
    (repo / "selected.txt").write_text("requested\n")
    (repo / "other.txt").write_text("another author\n")
    git(repo, "add", "other.txt")
    staged = git(repo, "show", ":other.txt")
    remote_head = git(remote, "rev-parse", "main")
    ok, _ = publish_changes(repo, commit_msg="selected", files_to_commit=["selected.txt"])
    assert not ok
    assert git(repo, "show", "--pretty=", "--name-only", "HEAD").splitlines() == ["selected.txt"]
    assert git(repo, "show", ":other.txt") == staged
    assert git(repo, "diff", "--cached", "--name-only").splitlines() == ["other.txt"]
    assert git(remote, "rev-parse", "main") == remote_head
