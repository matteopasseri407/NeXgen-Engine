"""Google OAuth for the engine's personal connectors: read the machine store.

Same store the private adapter uses, so one login serves both: tokens in
``~/.config/nexgen-workspace-mcp/tokens.json`` (0600, refresh + access +
expires_at), client secret from ``WORKSPACE_GOOGLE_CLIENT_SECRET`` or the
machine-local 0600 ``env`` file next to the tokens. Overrides via
``WORKSPACE_MCP_TOKEN_DIR``.

Never opens a browser: without a refresh token this raises ``NeedsLogin``
with the exact one-time step. Silent refresh only. The OAuth client is yours: nothing here
ships a default one (see ``client_id``).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import token_store

TOKEN_URL = "https://oauth2.googleapis.com/token"

class ConnectorError(RuntimeError):
    """A connector failure, carrying the lane refusal string."""

    def __init__(self, refusal: str) -> None:
        super().__init__(refusal)
        self.refusal = refusal


class AuthError(ConnectorError):
    """No usable access: not configured, or the backend refused us."""


class NeedsLogin(AuthError):
    """No tokens on this machine: the human runs the one-time login first."""


# Canonical HTTP helper lives in token_store; re-exported here because
# gmail/drive/calendar import it from .auth.
HttpResult = token_store.HttpResult
HttpFn = token_store.HttpFn


def _default_http(req, timeout: int) -> HttpResult:
    return token_store.default_http(req, timeout)


def token_dir() -> Path:
    return token_store.resolve_token_dir("WORKSPACE_MCP_TOKEN_DIR", "nexgen-workspace-mcp")


def token_file() -> Path:
    return token_dir() / "tokens.json"


def status() -> tuple[bool, str]:
    """Doctor helper: configured or not, without touching the network."""
    ok, detail = token_store.token_status(token_file(), "serve il login una tantum")
    if ok and not client_id():
        return False, "manca WORKSPACE_GOOGLE_CLIENT_ID (il tuo client OAuth: nessun client condiviso e' incluso)"
    return ok, detail


def _machine_env(name: str) -> str:
    return token_store.machine_env(token_dir(), name)


def client_id() -> str:
    """Your own Google OAuth client. There is deliberately no default: a client id shipped in a public
    repository would route every install's login through its author's Google Cloud project (his consent
    screen, his quota, his revocation switch). Create one in your own project and set
    ``WORKSPACE_GOOGLE_CLIENT_ID`` (environment or the machine-local ``env`` file)."""
    return _machine_env("WORKSPACE_GOOGLE_CLIENT_ID")


def client_secret() -> str:
    return _machine_env("WORKSPACE_GOOGLE_CLIENT_SECRET")


def load_tokens() -> dict[str, Any] | None:
    return token_store.load_tokens_from(token_file())


def save_tokens(data: dict[str, Any]) -> None:
    token_store.save_tokens_to(token_dir(), token_file(), data)


def refresh_tokens(tokens: dict[str, Any], http: HttpFn | None = None) -> dict[str, Any]:
    """Exchange the refresh token for a fresh access token; persists it."""
    if not client_id():
        raise AuthError(
            "(manca il client OAuth Google: crea il tuo nel tuo progetto Google Cloud e imposta "
            "WORKSPACE_GOOGLE_CLIENT_ID, nell'ambiente o nel file env accanto ai token)"
        )
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
        updated = token_store.refreshed_tokens(tokens, payload)
    except (ValueError, KeyError) as exc:
        raise AuthError("(accesso rifiutato dal provider: risposta illeggibile)") from exc
    save_tokens(updated)
    return updated


def access_token(http: HttpFn | None = None) -> str:
    """A valid access token, or NeedsLogin. Never opens a browser."""
    from nexgen_core.lock import HostLock, LockTimeoutError

    try:
        with HostLock(lock_path=token_file().with_suffix(".lock"), timeout=10, command_name="oauth-refresh"):
            tokens = load_tokens()
            if not tokens:
                raise NeedsLogin(
                    "(posta non configurata: nessun token su questa macchina, serve il login una tantum nel browser)"
                )
            access = str(tokens.get("access_token") or "")
            expires_at = int(tokens.get("expires_at") or 0)
            if access and expires_at > int(time.time()) + 60:
                return access
            # Re-read under lock: another process may have refreshed already.
            tokens = load_tokens() or tokens
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
    except NeedsLogin:
        raise
    except AuthError:
        raise
    except LockTimeoutError as exc:
        raise AuthError("(accesso non riuscito: token occupato da un altro processo, riprova)") from exc
