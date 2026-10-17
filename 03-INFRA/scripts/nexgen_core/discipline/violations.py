"""Shared result type and path filters for every gate."""
from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch

from .config import Config
from .gitutil import Change

SHELL_EXT = (".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd")
CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java", ".kt", ".rb",
            ".php", ".c", ".cc", ".cpp", ".h", ".cs", ".swift", ".lua", ".ex", ".exs", ".dart")


@dataclass(frozen=True)
class Violation:
    path: str
    message: str
    kind: str = ""  # file, function, shell, test, swallow

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)(test_[^/]*|conftest\.py)$|_test\.[^/]*$|\.(test|spec)\.[^/]*$")


def selected(change: Change, cfg: Config, extensions: tuple[str, ...]) -> bool:
    """A live (not deleted) change to a file of the wanted kind that is not excluded."""
    if change.new is None or not change.path.lower().endswith(extensions):
        return False
    return not any(fnmatch(change.path, pattern) for pattern in cfg.exclude)


def line_count(text: str | None) -> int:
    return len(text.splitlines()) if text else 0
