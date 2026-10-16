"""Limits, with optional overrides from `.nexgen-discipline.toml` at the repo root."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .gitutil import show

CONFIG_NAME = ".nexgen-discipline.toml"
COMMAND = "python 03-INFRA/scripts/discipline.py"


@dataclass(frozen=True)
class Config:
    max_file_lines: int = 500
    max_func_lines: int = 60
    shell_max_lines: int = 40
    shell_max_logic: int = 8
    complexity_threshold: int = 8
    review_paths: tuple[str, ...] = ()  # globs of production code; empty means no review is ever required
    review_exempt: tuple[str, ...] = ("*/tests/*", "tests/*", "*/test_*", "test_*")
    exclude: tuple[str, ...] = field(default_factory=lambda: (
        "*.lock", "*package-lock.json", "vendor/*", "*/vendor/*", "node_modules/*",
        "*/node_modules/*", "*.min.js", "*_pb2.py", "*/generated/*", "*/migrations/*",
    ))


def load(root: Path, base: str) -> Config:
    """Limits as committed at `base`: a change cannot loosen the rules it is judged by."""
    text = show(root, f"{base}:{CONFIG_NAME}")
    if text is None:
        return Config()
    data = tomllib.loads(text)
    known = {f.name for f in fields(Config)}
    values = {k: (tuple(v) if isinstance(v, list) else v) for k, v in data.items() if k in known}
    if "exclude" in values:  # a repository adds to the defaults, it does not replace them
        values["exclude"] = Config().exclude + values["exclude"]
    return Config(**values)
