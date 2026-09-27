"""Read-only Gmail over the REST API: search for ids, read one message whole.

Two calls, like the provider does it: ``messages.list`` returns ids only,
``messages.get`` returns one message. The lane resolves the sender/period,
searches, reads the selected conversations and keeps the ids: the model
never invents one, it only picks from what the engine found.

Refusals are lane strings in parentheses: empty search is "(nessun
risultato)", everything else names the failure so the engine reports ERROR,
never a false "no mail".
"""

from __future__ import annotations

import base64
import urllib.parse
import urllib.request
from typing import Any

from .auth import HttpFn, _default_http
from . import ConnectorError

_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _get(url: str, http: HttpFn, auth_http: HttpFn | None) -> Any:
    from . import auth as _auth  # local import: keeps cold paths dependency-free

    token = _auth.access_token(auth_http)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        result = http(req, 60)
    except OSError as exc:
        raise ConnectorError(f"(gmail non raggiungibile: {exc})") from exc
    if result.status == 401:
        raise ConnectorError("(gmail: accesso scaduto o revocato, serve un nuovo login)")
    if result.status != 200:
        raise ConnectorError(f"(gmail: ricerca fallita: HTTP {result.status})")
    import json

    try:
        return json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(gmail: risposta illeggibile dal provider)") from exc


def search_messages(query: str, max_results: int = 5, http: HttpFn | None = None) -> list[dict[str, Any]]:
    """Message ids (plus thread ids) matching a Gmail-style query."""
    query = str(query or "").strip()
    if not query:
        raise ConnectorError("(query vuota)")
    call = http or _default_http
    payload = _get(
        f"{_API}/messages?q={urllib.parse.quote(query)}&maxResults={max(1, min(int(max_results), 20))}",
        call,
        http,
    )
    items = payload.get("messages") or []
    return [
        {"id": str(item.get("id", "")), "threadId": str(item.get("threadId", ""))} for item in items if item.get("id")
    ]


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for header in (payload.get("payload") or {}).get("headers", []):
        name = str(header.get("name", "")).lower()
        if name in ("from", "to", "subject", "date", "message-id") and name not in found:
            found[name] = str(header.get("value", ""))
    return found


def _html_to_text(html: str) -> str:
    """Best-effort text out of an HTML part: tags out, entities decoded.

    Stdlib only, no parser dependency: strip tags, unescape entities,
    collapse whitespace. Formatting is lost on purpose; the coverage flag
    tells the reader the text came from HTML.
    """
    import html as _html
    import re

    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _walk_text(payload: dict[str, Any], attachments: list[str]) -> tuple[list[str], list[str]]:
    """Plain-text parts and HTML-derived text, kept apart for the coverage flag.

    Attachment names recorded, never fetched here. A part with a filename is
    an attachment even when its MIME is text/*: its bytes are the attachment,
    not the message body.
    """
    plain: list[str] = []
    html: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime = str(part.get("mimeType", ""))
        filename = str(part.get("filename", ""))
        if filename:
            attachments.append(f"{filename} ({mime or 'allegato'})")
        body = part.get("body") or {}
        data = body.get("data")
        if data and not filename:
            try:
                text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except ValueError:
                text = ""
            if text.strip():
                if mime.startswith("text/plain"):
                    plain.append(text)
                elif mime.startswith("text/html"):
                    stripped = _html_to_text(text)
                    if stripped:
                        html.append(stripped)
        for sub in part.get("parts", []):
            visit(sub)

    visit(payload.get("payload") or {})
    return plain, html


def get_message(mid: str, http: HttpFn | None = None) -> dict[str, Any]:
    """One message whole: headers, plain-text body, attachment names."""
    mid = str(mid or "").strip()
    if not mid:
        raise ConnectorError("(id messaggio vuoto)")
    call = http or _default_http
    payload = _get(f"{_API}/messages/{urllib.parse.quote(mid)}?format=full", call, http)
    if not isinstance(payload, dict) or "id" not in payload:
        raise ConnectorError("(gmail: messaggio non trovato)")
    headers = _headers(payload)
    attachments: list[str] = []
    plain, html = _walk_text(payload, attachments)
    snippet = str(payload.get("snippet", ""))
    if plain:
        # Full fidelity: the provider's own plain-text parts.
        body, coverage = "\n".join(part.strip() for part in plain if part.strip()), "text"
    elif html:
        # Readable but lossy: formatting gone, facts kept, flagged as such.
        body, coverage = "\n".join(part.strip() for part in html if part.strip()), "html"
    else:
        # Nothing extractable: the snippet is a short excerpt (Google's own
        # definition), never the whole message — flagged so the lane knows
        # this read is partial.
        body, coverage = snippet, "snippet"
    return {
        "id": str(payload.get("id", mid)),
        "threadId": str(payload.get("threadId", "")),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "message-id": headers.get("message-id", ""),
        "snippet": snippet,
        "body": body,
        "coverage": coverage,
        "attachments": attachments,
    }


def _post(path: str, payload: dict[str, Any], http: HttpFn, auth_http: HttpFn | None) -> dict[str, Any]:
    from . import auth as _auth

    token = _auth.access_token(auth_http)
    import json

    req = urllib.request.Request(f"{_API}{path}", data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        result = http(req, 60)
    except OSError as exc:
        raise ConnectorError(f"(gmail non raggiungibile: {exc})") from exc
    if result.status == 401:
        raise ConnectorError("(gmail: accesso scaduto o revocato, serve un nuovo login)")
    if result.status not in (200, 201):
        raise ConnectorError(f"(gmail: invio fallito: HTTP {result.status})")
    try:
        data = json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(gmail: risposta illeggibile dal provider)") from exc
    return data if isinstance(data, dict) else {}


def _raw_message(to: str, subject: str, body: str, cc: str = "") -> str:
    lines = [f"To: {to}", f"Subject: {subject}"]
    if cc:
        lines.append(f"Cc: {cc}")
    lines.extend(["", body])
    return base64.urlsafe_b64encode("\r\n".join(lines).encode("utf-8")).decode("ascii")


def send_message(to: str, subject: str, body: str, cc: str = "", http: HttpFn | None = None) -> dict[str, Any]:
    """Send a new message. The caller (compose gate) owns to/subject/body."""
    to, subject, body = str(to or "").strip(), str(subject or "").strip(), str(body or "")
    if not to or "@" not in to:
        raise ConnectorError("(gmail: destinatario mancante o non valido)")
    if not body.strip():
        raise ConnectorError("(gmail: corpo vuoto, niente da inviare)")
    call = http or _default_http
    sent = _post(
        "/messages/send", {"raw": _raw_message(to, subject or "(senza oggetto)", body, cc.strip())}, call, http
    )
    if not sent.get("id"):
        raise ConnectorError("(gmail: invio non confermato dal provider)")
    return {"id": str(sent["id"])}


def reply_to(message_id: str, body: str, http: HttpFn | None = None) -> dict[str, Any]:
    """Reply to an engine-retrieved message: envelope from the original.

    To/Subject/threading come from the original headers, never from the
    model: the model drafts only the body, the human approves the whole.
    """
    original = get_message(message_id, http)
    to = original.get("from", "")
    if not to:
        raise ConnectorError("(gmail: mittente originale illeggibile, risposta impossibile)")
    subject = original.get("subject", "") or "Re: "
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    lines = [f"To: {to}", f"Subject: {subject}"]
    thread_id = original.get("threadId", "")
    msg_id = str(original.get("message-id", ""))
    if msg_id:
        lines.append(f"In-Reply-To: {msg_id}")
        lines.append(f"References: {msg_id}")
    lines.extend(["", str(body or "")])
    raw = base64.urlsafe_b64encode("\r\n".join(lines).encode("utf-8")).decode("ascii")
    call = http or _default_http
    payload = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    sent = _post("/messages/send", payload, call, http)
    if not sent.get("id"):
        raise ConnectorError("(gmail: invio non confermato dal provider)")
    return {"id": str(sent["id"]), "to": to, "subject": subject}
