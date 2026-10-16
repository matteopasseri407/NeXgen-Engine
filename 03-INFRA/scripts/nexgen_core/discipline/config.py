"""Limits, with optional overrides from `.nexgen-discipline.toml` at the repo root."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .gitutil import show

CONFIG_NAME = ".nexgen-discipline.toml"


class ConfigError(RuntimeError):
    pass

COMMAND = "python 03-INFRA/scripts/discipline.py"


@dataclass(frozen=True)
class Config:
    max_file_lines: int = 500
    max_func_lines: int = 60
    shell_max_lines: int = 40
    shell_max_logic: int = 8
    complexity_threshold: int = 8  # a repository may lower it for its critical paths
    smoke_command: str = ""  # the one command that proves the program really works
    test_command: str = "python -m pytest -q"  # run on a fix's tests against the code before the fix; empty turns that proof off
    test_removal_ok: tuple[str, ...] = ()  # test paths the owner allowed to shrink or go
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
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{CONFIG_NAME} at {base} is not valid TOML ({err}). Fix that file; quote values with double quotes and escape backslashes.") from err
    known = {f.name for f in fields(Config)}
    values = {k: (tuple(v) if isinstance(v, list) else v) for k, v in data.items() if k in known}
    if "exclude" in values:  # a repository adds to the defaults, it does not replace them
        values["exclude"] = Config().exclude + values["exclude"]
    return Config(**values)
