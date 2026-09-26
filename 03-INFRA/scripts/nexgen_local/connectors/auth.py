"""Google OAuth for the engine's personal connectors: read the machine store.

Same store the private adapter uses, so one login serves both: tokens in
``~/.config/nexgen-workspace-mcp/tokens.json`` (0600, refresh + access +
expires_at), client secret from ``WORKSPACE_GOOGLE_CLIENT_SECRET`` or the
machine-local 0600 ``env`` file next to the tokens. Overrides via
``WORKSPACE_MCP_TOKEN_DIR``.

Never opens a browser: without a refresh token this raises ``NeedsLogin``
with the exact one-time step. Silent refresh only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Public identifier of the Google Cloud OAuth client (project
#: ``my-n8n-calendar-sync``). Not a secret; overridable per machine.
DEFAULT_CLIENT_ID = "684887243833-d5ufr6sb9boq08figiq51eapsfqu412e.apps.googleusercontent.com"


class ConnectorError(RuntimeError):
    """A connector failure, carrying the lane refusal string."""

    def __init__(self, refusal: str) -> None:
        super().__init__(refusal)
        self.refusal = refusal


class AuthError(ConnectorError):
    """No usable access: not configured, or the backend refused us."""


class NeedsLogin(AuthError):
    """No tokens on this machine: the human runs the one-time login first."""


@dataclass
class HttpResult:
    status: int
    body: bytes


def _default_http(req: urllib.request.Request, timeout: int) -> HttpResult:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResult(resp.status, resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResult(exc.code, exc.read()[:2000])


HttpFn = Callable[[urllib.request.Request, int], HttpResult]


def token_dir() -> Path:
    return Path(os.environ.get("WORKSPACE_MCP_TOKEN_DIR") or str(Path.home() / ".config" / "nexgen-workspace-mcp"))


def token_file() -> Path:
    return token_dir() / "tokens.json"


def status() -> tuple[bool, str]:
    """Doctor helper: configured or not, without touching the network."""
    path = token_file()
    if not path.is_file():
        return False, f"nessun token in {path} (serve il login una tantum)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, f"token illeggibili in {path}"
    if not isinstance(data, dict) or not data.get("refresh_token"):
        return False, f"token incompleti in {path}"
    return True, str(path)


def _machine_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    env_file = token_dir() / "env"
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


def client_id() -> str:
    return _machine_env("WORKSPACE_GOOGLE_CLIENT_ID") or DEFAULT_CLIENT_ID


def client_secret() -> str:
    return _machine_env("WORKSPACE_GOOGLE_CLIENT_SECRET")


def load_tokens() -> dict[str, Any] | None:
    path = token_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("refresh_token") else None


def save_tokens(data: dict[str, Any]) -> None:
    directory = token_dir()
    directory.mkdir(parents=True, exist_ok=True)
    token_file().write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(token_file(), 0o600)
    except OSError:
        pass


def refresh_tokens(tokens: dict[str, Any], http: HttpFn | None = None) -> dict[str, Any]:
    """Exchange the refresh token for a fresh access token; persists it."""
    fields = {
        "client_id": client_id(),
        "grant_type": "refresh_token",
        "refresh_token": str(tokens["refresh_token"]),
    }
    secret = client_secret()
    if secret:
        fields["client_secret"] = secret
    request = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(fields).encode("utf-8"), method="POST")
    call = http or _default_http
    try:
        result = call(request, 30)
    except OSError as exc:
        raise AuthError(f"(accesso non riuscito: {exc})") from exc
    if result.status != 200:
        raise AuthError(f"(accesso rifiutato dal provider: HTTP {result.status})")
    try:
        payload = json.loads(result.body.decode("utf-8", errors="replace"))
        access = str(payload["access_token"])
    except (ValueError, KeyError) as exc:
        raise AuthError("(accesso rifiutato dal provider: risposta illeggibile)") from exc
    updated = {
        **tokens,
        "access_token": access,
        "expires_at": int(time.time()) + int(payload.get("expires_in", 3600)),
    }
    save_tokens(updated)
    return updated


def access_token(http: HttpFn | None = None) -> str:
    """A valid access token, or NeedsLogin. Never opens a browser."""
    tokens = load_tokens()
    if not tokens:
        raise NeedsLogin(
            "(posta non configurata: nessun token su questa macchina, serve il login una tantum nel browser)"
        )
    access = str(tokens.get("access_token") or "")
    expires_at = int(tokens.get("expires_at") or 0)
    if access and expires_at > int(time.time()) + 60:
        return access
    try:
        return str(refresh_tokens(tokens, http)["access_token"])
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - any refresh surprise is an auth failure
        raise AuthError(f"(accesso non riuscito: {exc})") from exc
