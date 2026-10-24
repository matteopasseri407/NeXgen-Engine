"""Web search through the vendor's cloud API, with the user's own key.

The engine used to ship a self-hosted Firecrawl stack and call its wrapper; both
are gone, so this is the one road left: the same `FIRECRAWL_API_KEY` the MCP
connector already uses, read from the environment on every call and never stored
in the config object, never printed, and never written into the audit line (which
records the tool, the query and the length of the answer, nothing else).

One job: turn a query into the lines a person reads, or into a refusal that says
which piece is missing. Failures are `ConnectorError`s carrying the lane's display
message, because the lane decides ABSENCE and ERROR from the receipt, never from
the words.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from . import ConnectorError
from .token_store import HttpFn, HttpResult, default_http

#: The vendor's search endpoint. The API version is part of the path, so a new
#: major version is a change here and nowhere else.
SEARCH_URL = "https://api.firecrawl.dev/v2/search"

#: Where the key comes from. One name, shared with the MCP connector in
#: `agent-universal-layer/mcp/manifest.yaml`, so a machine configures it once.
KEY_ENV = "FIRECRAWL_API_KEY"

#: How long a search may take before the lane gives up on it.
DEFAULT_TIMEOUT = 30.0


class MissingKey(ConnectorError):
    """No key on this machine: the lane says so instead of calling a wrong URL."""


def api_key(env: dict[str, str] | None = None) -> str:
    """The key, or a refusal that names the variable. Never returned in a message."""
    value = (os.environ if env is None else env).get(KEY_ENV, "")
    if not value.strip():
        raise MissingKey(
            f"(ricerca web non configurata: manca {KEY_ENV}; mettila con 'nexgen-secrets set {KEY_ENV}')"
        )
    return value.strip()


def _request(query: str, limit: int, key: str) -> urllib.request.Request:
    body = json.dumps({"query": query, "limit": max(1, int(limit))}).encode("utf-8")
    request = urllib.request.Request(SEARCH_URL, data=body, method="POST")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Content-Type", "application/json")
    # The vendor answers JSON; asking for it explicitly keeps a proxy from
    # negotiating something this module does not parse.
    request.add_header("Accept", "application/json")
    return request


def _lines(payload: Any, limit: int) -> list[str]:
    """The answer as `title - url` lines, plus the first of its text when there is one.

    The vendor's shape carries the hits under `data` (web results in
    `data.web`, older responses directly in `data`); anything else is treated as
    an answer this module does not understand rather than guessed at.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    hits = data.get("web") if isinstance(data, dict) else data
    if not isinstance(hits, list):
        return []
    lines: list[str] = []
    for hit in hits[: max(1, int(limit))]:
        if not isinstance(hit, dict):
            continue
        title = str(hit.get("title") or "").strip()
        url = str(hit.get("url") or "").strip()
        description = str(hit.get("description") or "").strip()
        line = " - ".join(part for part in (title, url) if part)
        if description:
            line = f"{line}: {description}" if line else description
        if line:
            lines.append(line)
    return lines


def search(
    query: str,
    *,
    limit: int = 5,
    timeout: float = DEFAULT_TIMEOUT,
    http: HttpFn | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """The hits for one query. Raises `ConnectorError` with the lane's sentence."""
    key = api_key(env)
    call = http or default_http
    try:
        result: HttpResult = call(_request(query, limit, key), int(timeout))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # The exception text of a transport failure never carries the header,
        # and the key is not in the message this builds either.
        raise ConnectorError(f"(ricerca web non raggiungibile: {exc})") from exc
    if result.status in (401, 403):
        raise ConnectorError(f"(ricerca web rifiutata: la chiave in {KEY_ENV} non è valida)")
    if result.status == 429:
        raise ConnectorError("(ricerca web: troppe richieste, riprova fra poco)")
    if result.status != 200:
        raise ConnectorError(f"(ricerca web fallita: HTTP {result.status})")
    try:
        payload = json.loads(result.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ConnectorError("(ricerca web: risposta illeggibile dal provider)") from exc
    return _lines(payload, limit)
