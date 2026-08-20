"""The stack's secrets: generated if missing, never overwritten, never printed.

A secret that's already present is never touched: regenerating it would
disconnect data already encrypted with the old one. And no function here
returns a value through a user-facing message or a log: they get written
to a file with tight permissions and read from there.
"""
from __future__ import annotations

import os
import secrets as _secrets
import stat
from pathlib import Path


def generate() -> str:
    """A new secret, the same shape as `openssl rand -hex 32`."""
    return _secrets.token_hex(32)


def read_env_file(path: Path) -> dict[str, str]:
    """Reads a dotenv-style file, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _restrict(path: Path) -> None:
    """Tight permissions: the file contains operational keys."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # On a filesystem that doesn't support them there's nothing to do,
        # and failing here would block startup over an inapplicable detail.
        pass


def ensure(path: Path, names: list[str]) -> list[str]:
    """Ensures every name has a value in the file, generating it if missing.

    Returns the NAMES of what it generated, never the values.
    """
    existing = read_env_file(path)
    missing = [n for n in names if not existing.get(n)]
    if not missing:
        _restrict(path)
        return []

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    generated: list[str] = []
    for name in missing:
        value = generate()
        for index, line in enumerate(lines):
            if line.strip().startswith(f"{name}="):
                lines[index] = f"{name}={value}"
                break
        else:
            lines.append(f"{name}={value}")
        generated.append(name)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _restrict(path)
    return generated


def resolve_exports(exports: dict[str, str], port: int, env_values: dict[str, str]) -> dict[str, str]:
    """Expands a service's placeholders into the real values for the workstation."""
    resolved: dict[str, str] = {}
    for key, template in exports.items():
        value = template.replace("{porta}", str(port))
        if value.startswith("{token:") and value.endswith("}"):
            token_name = value[len("{token:"):-1]
            token_value = env_values.get(token_name, "")
            if not token_value:
                continue
            value = token_value
        resolved[key] = value
    return resolved


def write_workstation_env(path: Path, values: dict[str, str]) -> None:
    """Writes the variables the runtimes will read, with tight permissions.

    The file is generated: it gets rewritten in full on every pass, so an
    entry removed from the stack actually disappears instead of being left
    pointing at nothing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# NeXgen Engine — automatically generated, do not edit by hand.",
        "# Connector variables for a stack running on this machine.",
        "",
    ]
    body += [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    _restrict(path)


def workstation_env_path(home: Path | None = None) -> Path:
    """Where the connector variables end up on this machine."""
    base = home or Path.home()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else base / ".config"
    return root / "environment.d" / "90-nexgen-stack.conf"
