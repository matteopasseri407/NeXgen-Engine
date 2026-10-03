"""Read and draft Outlook mail over Microsoft Graph, same lane contract as Gmail.

Reads (search/get) feed the loop; send/reply exist here as provider calls
but only the compose gate invokes them, after human approval. Auth is Entra
loopback against the customer's own app registration: client and tenant come
from the environment or the machine-local 0600 files, tokens live in their
own machine-local store. No tokens means NeedsLogin, never a browser.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import AuthError, ConnectorError, NeedsLogin
from . import token_store as _store

GRAPH = "https://graph.microsoft.com/v1.0"


# Single HTTP implementation lives in token_store; kept here as aliases so
# existing `outlook.HttpResult` imports keep working.
HttpResult = _store.HttpResult
HttpFn = _store.HttpFn


def _default_http(req, timeout: int) -> HttpResult:
    return _store.default_http(req, timeout)


def token_dir() -> Path:
    return _store.resolve_token_dir("OUTLOOK_TOKEN_DIR", "nexgen-outlook")


def token_file() -> Path:
    return token_dir() / "tokens.json"


def status() -> tuple[bool, str]:
    """Doctor helper: configured or not, without touching the network."""
    return _store.token_status(token_file(), "registrazione Azure + login una tantum")


def _machine_env(name: str) -> str:
    return _store.machine_env(token_dir(), name)


def client_id() -> str:
    return _machine_env("OUTLOOK_CLIENT_ID")


def tenant_id() -> str:
    return _machine_env("OUTLOOK_TENANT_ID") or "common"


def client_secret() -> str:
    return _machine_env("OUTLOOK_CLIENT_SECRET")


def token_url() -> str:
    return f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id())}/oauth2/v2.0/token"


def load_tokens() -> dict[str, Any] | None:
    return _store.load_tokens_from(token_file())


def save_tokens(data: dict[str, Any]) -> None:
    _store.save_tokens_to(token_dir(), token_file(), data)


def refresh_tokens(tokens: dict[str, Any], http: HttpFn | None = None) -> dict[str, Any]:
    fields = {
        "client_id": client_id(),
        "grant_type": "refresh_token",
        "refresh_token": str(tokens["refresh_token"]),
        "scope": "openid profile email offline_access Mail.ReadWrite Mail.Send",
    }
    secret = client_secret()
    if secret:
        fields["client_secret"] = secret
    request = urllib.request.Request(token_url(), data=urllib.parse.urlencode(fields).encode("utf-8"), method="POST")
    call = http or _default_http
    try:
        result = call(request, 30)
    except OSError as exc:
        raise AuthError(f"(accesso non riuscito: {exc})") from exc
    if result.status != 200:
        raise AuthError(f"(accesso rifiutato dal provider: HTTP {result.status})")
    try:
        payload = json.loads(result.body.decode("utf-8", errors="replace"))
        updated = _store.refreshed_tokens(tokens, payload)
    except (ValueError, KeyError) as exc:
        raise AuthError("(accesso rifiutato dal provider: risposta illeggibile)") from exc
    save_tokens(updated)
    return updated


def access_token(http: HttpFn | None = None) -> str:
    """A valid access token, or NeedsLogin. Never opens a browser."""
    if not client_id():
        raise NeedsLogin("(outlook non configurato: serve la registrazione app in Azure + login una tantum)")
    tokens = load_tokens()
    if not tokens:
        raise NeedsLogin("(outlook non configurato: nessun token su questa macchina, serve il login una tantum)")
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


def _get(url: str, http: HttpFn, auth_http: HttpFn | None) -> Any:
    token = access_token(auth_http)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        result = http(req, 60)
    except OSError as exc:
        raise ConnectorError(f"(outlook non raggiungibile: {exc})") from exc
    if result.status == 401:
        raise ConnectorError("(outlook: accesso scaduto o revocato, serve un nuovo login)")
    if result.status != 200:
        raise ConnectorError(f"(outlook: ricerca fallita: HTTP {result.status})")
    try:
        return json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(outlook: risposta illeggibile dal provider)") from exc


def search_messages(query: str, max_results: int = 5, http: HttpFn | None = None) -> list[dict[str, Any]]:
    """Message ids matching a query (Graph $search)."""
    query = str(query or "").strip()
    if not query:
        raise ConnectorError("(query vuota)")
    call = http or _default_http
    params = urllib.parse.urlencode(
        {
            "$search": f'"{query}"',
            "$top": max(1, min(int(max_results), 20)),
            "$select": "id,subject,from,receivedDateTime",
        }
    )
    payload = _get(f"{GRAPH}/me/messages?{params}", call, http)
    items = payload.get("value") or []
    return [{"id": str(item.get("id", ""))} for item in items if item.get("id")]


def get_message(mid: str, http: HttpFn | None = None) -> dict[str, Any]:
    """One message whole: headers, plain-text body, attachment names."""
    mid = str(mid or "").strip()
    if not mid:
        raise ConnectorError("(id messaggio vuoto)")
    call = http or _default_http
    params = urllib.parse.urlencode(
        {"$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,hasAttachments"}
    )
    payload = _get(f"{GRAPH}/me/messages/{urllib.parse.quote(mid)}?{params}", call, http)
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ConnectorError("(outlook: messaggio non trovato)")
    body = payload.get("body") or {}
    raw = str(body.get("content", "") or "")
    preview = str(payload.get("bodyPreview", ""))
    if raw.strip() and body.get("contentType", "text").lower() == "html":
        import re

        stripped = re.sub(r"<[^>]+>", " ", raw)
        text, coverage = re.sub(r"\s+", " ", stripped).strip(), "html"
    elif raw.strip():
        text, coverage = raw, "text"
    else:
        # Preview only: a short excerpt, never the whole message.
        text, coverage = preview, "snippet"
    sender = payload.get("from") or {}
    return {
        "id": str(payload.get("id", mid)),
        "from": str((sender.get("emailAddress") or {}).get("address", "")),
        "to": ", ".join(
            str((each.get("emailAddress") or {}).get("address", "")) for each in (payload.get("toRecipients") or [])
        ),
        "subject": str(payload.get("subject", "")),
        "date": str(payload.get("receivedDateTime", "")),
        "snippet": preview,
        "body": text or preview,
        "coverage": coverage if (text or preview) else "snippet",
        "attachments": ["(allegati presenti)"] if payload.get("hasAttachments") else [],
    }


def _post(path: str, payload: dict[str, Any], http: HttpFn, auth_http: HttpFn | None) -> dict[str, Any]:
    token = access_token(auth_http)
    req = urllib.request.Request(f"{GRAPH}{path}", data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        result = http(req, 60)
    except OSError as exc:
        raise ConnectorError(f"(outlook non raggiungibile: {exc})") from exc
    if result.status == 401:
        raise ConnectorError("(outlook: accesso scaduto o revocato, serve un nuovo login)")
    if result.status not in (200, 201, 202):
        raise ConnectorError(f"(outlook: invio fallito: HTTP {result.status})")
    if not result.body.strip():
        return {}
    try:
        data = json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(outlook: risposta illeggibile dal provider)") from exc
    return data if isinstance(data, dict) else {}


def _address(value: str) -> dict[str, Any]:
    return {"emailAddress": {"address": value}}


def send_message(to: str, subject: str, body: str, cc: str = "", http: HttpFn | None = None) -> dict[str, Any]:
    """Send a new message. The caller (compose gate) owns to/subject/body."""
    to, subject, body = str(to or "").strip(), str(subject or "").strip(), str(body or "")
    if not to or "@" not in to:
        raise ConnectorError("(outlook: destinatario mancante o non valido)")
    if not body.strip():
        raise ConnectorError("(outlook: corpo vuoto, niente da inviare)")
    message: dict[str, Any] = {
        "subject": subject or "(senza oggetto)",
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [_address(to)],
    }
    if cc.strip():
        message["ccRecipients"] = [_address(cc.strip())]
    call = http or _default_http
    sent = _post("/me/sendMail", {"message": message}, call, http)
    return {"id": str(sent.get("id", "inviato"))}


def reply_to(message_id: str, body: str, http: HttpFn | None = None) -> dict[str, Any]:
    """Reply to an engine-retrieved message: Graph threads it server-side."""
    original = get_message(message_id, http)
    if not original.get("id"):
        raise ConnectorError("(outlook: originale illeggibile, risposta impossibile)")
    call = http or _default_http
    _post(
        f"/me/messages/{urllib.parse.quote(str(original['id']))}/reply",
        {"comment": str(body or "")},
        call,
        http,
    )
    return {"id": str(original["id"]), "to": original.get("from", ""), "subject": original.get("subject", "")}
