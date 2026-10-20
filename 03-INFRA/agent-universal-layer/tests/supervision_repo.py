"""A real repository with a bare remote, for the supervision tests.

The unit commands act on git for real: a fake would prove nothing about the
push block, the remote snapshot or the per-commit proof. Each test builds a
throwaway repository, a bare `origin` beside it, and a `main` that is already
published, so a unit's branch is the only thing that is not.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

#: The base a unit starts from: docs to edit, a module, and two real tests to weaken.
BASE_FILES = {
    "README.md": "# prova\n",
    "docs/guida.md": "# guida\n\nprima riga.\n",
    "module.py": "def value():\n    return 1\n",
    "pytest.ini": "[pytest]\ntestpaths = tests\npythonpath = .\n",
    "tests/test_sample.py": (
        "from module import value\n"
        "\n"
        "def test_value():\n"
        "    assert value() == 1\n"
        "\n"
        "def test_nome():\n"
        "    assert 'prova' == 'prova'\n"
    ),
}


def git_in(folder: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """One git command in a real folder, captured, with the engine's encoding."""
    return subprocess.run(
        ["git", "-C", str(folder), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


class Repo:
    """A repository, its bare remote, and the base files both start from."""

    def __init__(self, path: Path, bare: Path) -> None:
        self.path = path
        self.bare = bare

    def git(self, *args: str) -> str:
        return git_in(self.path, *args).stdout

    def write(self, name: str, text: str) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message: str = "c") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def remote_refs(self) -> str:
        """What the bare remote holds right now, as git prints it."""
        return self.git("ls-remote", "origin")


def build(root: Path) -> Repo:
    """One published commit on `main`, a bare `origin` beside it, and the base files."""
    bare = root / "upstream.git"
    path = root / "repo"
    git_in(root.parent, "init", "-q", "--bare", str(bare))
    git_in(root.parent, "init", "-q", "-b", "main", str(path))
    repo = Repo(path, bare)
    repo.git("config", "user.email", "t@example.com")
    repo.git("config", "user.name", "t")
    repo.git("config", "commit.gpgsign", "false")
    for name, text in BASE_FILES.items():
        repo.write(name, text)
    repo.commit("base")
    repo.git("remote", "add", "origin", str(bare))
    repo.git("push", "-q", "-u", "origin", "main")
    return repo
