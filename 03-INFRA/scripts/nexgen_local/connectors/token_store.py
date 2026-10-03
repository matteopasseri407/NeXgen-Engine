"""Single owner for machine-local OAuth token stores.

Google (auth.py) and Outlook (outlook.py) grew the same file/env/persist
helpers by copy-paste: same shape, divergent env names and defaults. Any fix
to permissions, parsing or refresh persistence had to land twice, and missed
once. This module owns the mechanics; providers keep only their names,
defaults and user-facing hints as parameters.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class HttpResult:
    status: int
    body: bytes


def default_http(req: urllib.request.Request, timeout: int) -> HttpResult:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResult(resp.status, resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResult(exc.code, exc.read()[:2000])


HttpFn = Callable[[urllib.request.Request, int], HttpResult]


def resolve_token_dir(env_name: str, default_subdir: str) -> Path:
    return Path(os.environ.get(env_name) or str(Path.home() / ".config" / default_subdir))


def machine_env(token_dir: Path, name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    env_file = token_dir / "env"
    if env_file.is_file():
        try:
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    if key.strip() == name:
                        return val.strip()
        except OSError:
            pass
    return ""


def load_tokens_from(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("refresh_token") else None


def save_tokens_to(directory: Path, path: Path, data: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def token_status(path: Path, missing_hint: str) -> tuple[bool, str]:
    """Doctor helper: configured or not, without touching the network."""
    if not path.is_file():
        return False, f"nessun token in {path} ({missing_hint})"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, f"token illeggibili in {path}"
    if not isinstance(data, dict) or not data.get("refresh_token"):
        return False, f"token incompleti in {path}"
    return True, str(path)
