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
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from nexgen_core.files import write_private_text


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
    if Path(path).parent != Path(directory):
        raise ValueError("token file must belong to its declared directory")
    write_private_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def refreshed_tokens(tokens: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Keep provider fields and persist a rotated refresh token when supplied."""
    if not isinstance(payload, dict):
        raise ValueError("token response must be an object")
    access = payload.get("access_token")
    refresh = payload.get("refresh_token") or tokens["refresh_token"]
    if not isinstance(access, str) or not access.strip():
        raise ValueError("access token is missing or invalid")
    if not isinstance(refresh, str) or not refresh.strip():
        raise ValueError("refresh token is missing or invalid")
    try:
        lifetime = int(payload.get("expires_in", 3600))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("token expiry is invalid") from exc
    if lifetime < 0:
        raise ValueError("token expiry is negative")
    return {
        **tokens,
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time.time()) + lifetime,
    }


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
