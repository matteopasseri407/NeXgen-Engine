"""Read-only Drive over the v3 REST API: search files, read text out of them.

Google Docs/Sheets/Slides are exported to text; plain-text files are
downloaded; anything else (PDF binaries included, for now) is refused with a
named reason instead of guessed bytes. No Drive code existed anywhere before
this module: not in the engine, not in the private adapter (which only
carries the ``drive.readonly`` scope string).

Refusals follow the lane taxonomy: empty search is "(nessun risultato)",
everything else is an error the engine reports as ERROR.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

from . import ConnectorError
from .auth import HttpFn, _default_http

_API = "https://www.googleapis.com/drive/v3"

#: Google Docs editors types mapped to their text export flavour.
_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

_FIELDS = "files(id,name,mimeType,modifiedTime,size)"


def _get(url: str, http: HttpFn, auth_http: HttpFn | None) -> Any:
    from . import auth as _auth  # local import: keeps cold paths dependency-free

    token = _auth.access_token(auth_http)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        result = http(req, 60)
    except OSError as exc:
        raise ConnectorError(f"(drive non raggiungibile: {exc})") from exc
    if result.status == 401:
        raise ConnectorError("(drive: accesso scaduto o revocato, serve un nuovo login)")
    if result.status != 200:
        raise ConnectorError(f"(drive: ricerca fallita: HTTP {result.status})")
    import json

    try:
        return json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(drive: risposta illeggibile dal provider)") from exc


def _raw(url: str, http: HttpFn, auth_http: HttpFn | None) -> tuple[int, bytes]:
    """Status plus raw bytes through the injected http layer (tests fake it)."""
    from . import auth as _auth

    token = _auth.access_token(auth_http)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        result = http(req, 60)
    except OSError as exc:
        raise ConnectorError(f"(drive non raggiungibile: {exc})") from exc
    return result.status, result.body


def search_files(query: str, max_results: int = 5, http: HttpFn | None = None) -> list[dict[str, Any]]:
    """Files whose name or content matches; never trashed ones."""
    query = str(query or "").strip()
    if not query:
        raise ConnectorError("(query vuota)")
    call = http or _default_http
    safe = query.replace("'", "\\'")
    q = f"trashed = false and (name contains '{safe}' or fullText contains '{safe}')"
    params = urllib.parse.urlencode(
        {"q": q, "fields": _FIELDS, "pageSize": max(1, min(int(max_results), 20)), "orderBy": "modifiedTime desc"}
    )
    payload = _get(f"{_API}/files?{params}", call, http)
    items = payload.get("files") or []
    return [
        {
            "id": str(item.get("id", "")),
            "name": str(item.get("name", "")),
            "mimeType": str(item.get("mimeType", "")),
            "modifiedTime": str(item.get("modifiedTime", "")),
        }
        for item in items
        if item.get("id")
    ]


def read_file(file_id: str, meta: dict[str, Any] | None = None, http: HttpFn | None = None) -> dict[str, Any]:
    """Text out of one file: export for Docs editors, download for text."""
    file_id = str(file_id or "").strip()
    if not file_id:
        raise ConnectorError("(id file vuoto)")
    call = http or _default_http
    if meta is None:
        params = urllib.parse.urlencode({"fields": "id,name,mimeType,modifiedTime,size"})
        meta = _get(f"{_API}/files/{urllib.parse.quote(file_id)}?{params}", call, http)
    if not isinstance(meta, dict) or not meta.get("id"):
        raise ConnectorError("(drive: file non trovato)")
    mime = str(meta.get("mimeType", ""))
    name = str(meta.get("name", file_id))
    if mime in _EXPORT_MIME:
        export = urllib.parse.urlencode({"mimeType": _EXPORT_MIME[mime]})
        status, raw = _raw(f"{_API}/files/{urllib.parse.quote(file_id)}/export?{export}", call, http)
        if status == 401:
            raise ConnectorError("(drive: accesso scaduto o revocato, serve un nuovo login)")
        if status != 200:
            raise ConnectorError(f"(drive: lettura fallita: HTTP {status})")
        text = raw.decode("utf-8", errors="replace")
        return {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "modifiedTime": str(meta.get("modifiedTime", "")),
            "text": text,
        }
    if mime.startswith("text/"):
        status, raw = _raw(f"{_API}/files/{urllib.parse.quote(file_id)}?alt=media", call, http)
        if status == 401:
            raise ConnectorError("(drive: accesso scaduto o revocato, serve un nuovo login)")
        if status != 200:
            raise ConnectorError(f"(drive: lettura fallita: HTTP {status})")
        return {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "modifiedTime": str(meta.get("modifiedTime", "")),
            "text": raw.decode("utf-8", errors="replace"),
        }
    raise ConnectorError(f"(drive: formato non leggibile come testo: {mime or 'sconosciuto'})")


#: Refuse uploads above this: multipart in memory, and no one stages
#: gigabytes by accident through an agent.
MAX_UPLOAD_BYTES = 100_000_000

#: Same cap for downloads into memory.
MAX_DOWNLOAD_BYTES = 100_000_000


def download_bytes(file_id: str, http: HttpFn | None = None) -> bytes:
    """Raw bytes of any Drive file, up to the cap. Text decisions stay upstream."""
    file_id = str(file_id or "").strip()
    if not file_id:
        raise ConnectorError("(id file vuoto)")
    call = http or _default_http
    status, raw = _raw(f"{_API}/files/{urllib.parse.quote(file_id)}?alt=media", call, http)
    if status == 401:
        raise ConnectorError("(drive: accesso scaduto o revocato, serve un nuovo login)")
    if status != 200:
        raise ConnectorError(f"(drive: lettura fallita: HTTP {status})")
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ConnectorError(f"(drive: file troppo grande ({len(raw)} byte))")
    return raw


def upload_file(
    data: bytes,
    name: str,
    mime_type: str = "",
    folder_id: str = "",
    http: HttpFn | None = None,
) -> dict[str, Any]:
    """Upload bytes as a new Drive file. The caller owns name and bytes.

    Multipart/related to ``/upload/drive/v3/files``. Returns the provider
    ``id`` (and name echo). Failures are lane refusals, never partial state
    the caller could mistake for success.
    """
    name = str(name or "").strip().strip("/").split("/")[-1]
    if not name:
        raise ConnectorError("(drive: nome file mancante)")
    if not data:
        raise ConnectorError("(drive: contenuto vuoto, niente da caricare)")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ConnectorError(f"(drive: file troppo grande ({len(data)} byte))")
    import json
    import uuid

    from . import auth as _auth

    call = http or _default_http
    token = _auth.access_token(http)
    metadata: dict[str, Any] = {"name": name}
    if mime_type:
        metadata["mimeType"] = mime_type
    if folder_id.strip():
        metadata["parents"] = [folder_id.strip()]
    boundary = f"nexgen-{uuid.uuid4().hex}"
    body = (
        (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\nContent-Type: {mime_type or 'application/octet-stream'}\r\n\r\n"
        ).encode("utf-8")
        + bytes(data)
        + f"\r\n--{boundary}--".encode("utf-8")
    )
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        data=body,
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f'multipart/related; boundary="{boundary}"')
    try:
        result = call(req, 120)
    except OSError as exc:
        raise ConnectorError(f"(drive non raggiungibile: {exc})") from exc
    if result.status == 401:
        raise ConnectorError("(drive: accesso scaduto o revocato, serve un nuovo login)")
    if result.status not in (200, 201):
        raise ConnectorError(f"(drive: caricamento fallito: HTTP {result.status})")
    try:
        payload = json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(drive: risposta illeggibile dal provider)") from exc
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ConnectorError("(drive: caricamento non confermato dal provider)")
    return {"id": str(payload["id"]), "name": str(payload.get("name", name))}
