import subprocess
from pathlib import Path

import pytest


class Repo:
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def write(self, name: str, text: str) -> None:
        file = self.path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def commit(self, message: str = "c") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = Repo(tmp_path)
    r.git("init", "-q", "-b", "main")
    r.git("config", "user.email", "t@example.com")
    r.git("config", "user.name", "t")
    r.git("config", "commit.gpgsign", "false")
    r.write("README.md", "hi\n")
    r.commit("init")
    monkeypatch.chdir(tmp_path)
    return r


def production(repo, globs='["*"]'):
    """Declare production paths on the base, as a repository would, so reviews can be required."""
    repo.write(".nexgen-discipline.toml", f"review_paths = {globs}\n")
    repo.commit("declare production paths")
