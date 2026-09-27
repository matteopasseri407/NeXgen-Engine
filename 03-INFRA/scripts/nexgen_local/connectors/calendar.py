"""Read and write Calendar over the v3 REST API, through the same OAuth store.

Reads (list/get) are plain functions. Writes (create/update/delete) exist
here as provider calls, but the lane never calls them directly: they go
through the calendar gate (``calendars.py``), propose first, human approval,
exactly like mail. No write happens from a model choice, ever.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

from . import ConnectorError
from .auth import HttpFn, _default_http

_API = "https://www.googleapis.com/calendar/v3"


def _call(path: str, http: HttpFn, auth_http: HttpFn | None, method: str = "GET", body: Any = None) -> Any:
    from . import auth as _auth

    token = _auth.access_token(auth_http)
    import json

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{_API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        result = http(req, 60)
    except OSError as exc:
        raise ConnectorError(f"(calendario non raggiungibile: {exc})") from exc
    if result.status == 401:
        raise ConnectorError("(calendario: accesso scaduto o revocato, serve un nuovo login)")
    # Delete answers 204 No Content on success: no body, no JSON, still done.
    if result.status not in (200, 201, 204):
        raise ConnectorError(f"(calendario: operazione fallita: HTTP {result.status})")
    if not result.body.strip():
        return {}
    try:
        return json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(calendario: risposta illeggibile dal provider)") from exc


def list_events(
    calendar_id: str = "primary",
    time_min: str = "",
    time_max: str = "",
    max_results: int = 10,
    http: HttpFn | None = None,
    q: str = "",
) -> list[dict[str, Any]]:
    """Upcoming events in the window, optionally pre-filtered server-side.

    ``q`` is the provider's free-text search (summary, description,
    location): filtering server-side first is what keeps an event past the
    page limit visible. Callers still refine locally afterwards.
    """
    call = http or _default_http
    params: dict[str, str] = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max(1, min(int(max_results), 50))),
    }
    if str(q or "").strip():
        params["q"] = str(q).strip()
    query = urllib.parse.urlencode(params)
    payload = _call(f"/calendars/{urllib.parse.quote(calendar_id)}/events?{query}", call, http)
    return payload.get("items", []) if isinstance(payload, dict) else []


def get_event(calendar_id: str = "primary", event_id: str = "", http: HttpFn | None = None) -> dict[str, Any]:
    if not str(event_id or "").strip():
        raise ConnectorError("(id evento vuoto)")
    call = http or _default_http
    payload = _call(
        f"/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(str(event_id))}",
        call,
        http,
    )
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ConnectorError("(calendario: evento non trovato)")
    return payload


def when(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("dateTime") or value.get("date") or "")
    return str(value or "")


def describe_event(item: dict[str, Any]) -> str:
    """One honest line per event: time, title, location when present."""
    return (
        f"{item.get('id', '')} | {when(item.get('start'))} -> {when(item.get('end'))} | "
        f"{item.get('summary', '(senza titolo)')} | {item.get('location', '')}".rstrip(" |")
    )


def create_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    http: HttpFn | None = None,
) -> dict[str, Any]:
    """Create an event. Only the calendar gate calls this, after approval."""
    summary, start, end = str(summary or "").strip(), str(start or "").strip(), str(end or "").strip()
    if not summary:
        raise ConnectorError("(calendario: titolo mancante)")
    if not start or not end:
        raise ConnectorError("(calendario: inizio/fine mancanti, ISO 8601 con timezone)")
    payload: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if description:
        payload["description"] = description
    if location:
        payload["location"] = location
    call = http or _default_http
    created = _call(f"/calendars/{urllib.parse.quote(calendar_id)}/events", call, http, "POST", payload)
    if not isinstance(created, dict) or not created.get("id"):
        raise ConnectorError("(calendario: creazione non confermata dal provider)")
    return {"id": str(created["id"]), "summary": summary}


def delete_event(calendar_id: str = "primary", event_id: str = "", http: HttpFn | None = None) -> dict[str, Any]:
    """Delete an event. Only the calendar gate calls this, after approval."""
    if not str(event_id or "").strip():
        raise ConnectorError("(id evento vuoto)")
    call = http or _default_http
    _call(
        f"/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(str(event_id))}",
        call,
        http,
        "DELETE",
    )
    return {"id": str(event_id), "deleted": True}
