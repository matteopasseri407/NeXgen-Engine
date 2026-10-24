"""The lane's web search: the vendor's cloud API, the user's key, and no binary.

The engine used to run a self-hosted Firecrawl wrapper. It is gone, so these
tests pin the only road left: a POST to the cloud endpoint with
`FIRECRAWL_API_KEY` from the environment, a deadline, and a refusal that says
which piece is missing. Every test hands in a fake transport: nothing here opens
a socket, and nothing here launches a process.
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.connectors import web as web_conn
from nexgen_local.connectors.token_store import HttpResult
from nexgen_local.tools import ToolRegistry

FAKE_KEY = "fc-test-key-never-real"


class Recorder:
    """The fake transport: records every request, answers with what it was told."""

    def __init__(self, status: int = 200, body: bytes = b"{}", fail: Exception | None = None) -> None:
        self.status = status
        self.body = body
        self.fail = fail
        self.requests: list[tuple[object, int]] = []

    def __call__(self, request, timeout: int) -> HttpResult:
        self.requests.append((request, timeout))
        if self.fail is not None:
            raise self.fail
        return HttpResult(self.status, self.body)

    @property
    def only(self):
        assert len(self.requests) == 1, self.requests
        return self.requests[0][0]


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return LaneConfig(vault_root=vault, repo_roots=(), model="fake-model",
                      audit_path=tmp_path / "audit.jsonl")


def _payload(*hits: dict) -> bytes:
    return json.dumps({"success": True, "data": {"web": list(hits)}}).encode("utf-8")


def test_a_query_becomes_the_hits_the_cloud_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    http = Recorder(body=_payload(
        {"title": "Falco Rosso", "url": "https://example.test/falco", "description": "un vino"},
        {"title": "Solo titolo"},
    ))
    tools = ToolRegistry(_cfg(tmp_path), http=http)

    output = tools.web_search("falco rosso")

    assert "Falco Rosso - https://example.test/falco: un vino" in output
    assert "Solo titolo" in output
    assert tools.calls[-1].ok is True
    request = http.only
    assert request.full_url == web_conn.SEARCH_URL
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {"query": "falco rosso", "limit": 5}


def test_the_key_travels_in_the_header_and_never_in_the_answer_or_the_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    http = Recorder(body=_payload({"title": "Falco", "url": "https://example.test"}))
    cfg = _cfg(tmp_path)

    output = ToolRegistry(cfg, http=http).web_search("falco")

    assert http.only.get_header("Authorization") == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in output
    assert FAKE_KEY not in cfg.audit_path.read_text(encoding="utf-8")


def test_without_a_key_the_tool_refuses_and_calls_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    http = Recorder()
    tools = ToolRegistry(_cfg(tmp_path), http=http)

    output = tools.web_search("falco")

    assert output.startswith("(ricerca web non configurata")
    assert "FIRECRAWL_API_KEY" in output
    assert http.requests == [], "no key, no call"
    assert tools.calls[-1].ok is False


def test_the_deadline_is_the_one_the_configuration_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    http = Recorder(body=_payload({"title": "Falco"}))
    cfg = LaneConfig(vault_root=tmp_path / "vault", repo_roots=(), model="fake-model",
                     audit_path=tmp_path / "audit.jsonl", firecrawl_timeout=7.0)
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)

    ToolRegistry(cfg, http=http).web_search("falco")

    assert http.requests[0][1] == 7


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "(ricerca web rifiutata"),
        (403, "(ricerca web rifiutata"),
        (429, "(ricerca web: troppe richieste"),
        (500, "(ricerca web fallita: HTTP 500)"),
    ],
)
def test_a_provider_that_says_no_is_a_refusal_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    tools = ToolRegistry(_cfg(tmp_path), http=Recorder(status=status, body=b"{}"))

    output = tools.web_search("falco")

    assert output.startswith(expected)
    assert FAKE_KEY not in output
    assert tools.calls[-1].ok is False


def test_a_transport_that_cannot_reach_the_cloud_is_a_refusal_not_an_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    tools = ToolRegistry(_cfg(tmp_path), http=Recorder(fail=urllib.error.URLError("conn refused")))

    output = tools.web_search("falco")

    assert output.startswith("(ricerca web non raggiungibile")
    assert FAKE_KEY not in output
    assert tools.calls[-1].ok is False


def test_an_answer_with_no_hits_is_an_empty_receipt_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    tools = ToolRegistry(_cfg(tmp_path), http=Recorder(body=_payload()))

    output = tools.web_search("falco")

    assert output == "(nessun risultato)"
    assert tools.calls[-1].status == "empty"


def test_an_unreadable_answer_names_the_provider_instead_of_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    tools = ToolRegistry(_cfg(tmp_path), http=Recorder(body=b"<html>not json</html>"))

    output = tools.web_search("falco")

    assert output.startswith("(ricerca web: risposta illeggibile")
    assert tools.calls[-1].ok is False
