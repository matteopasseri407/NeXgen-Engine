"""Il groom non chiede di compattare una sessione archiviata, e un rifiuto non gli spegne il job.

nexgen: one-job reason="riprodurre il guasto di OpenClaw 2026.11.10: le archiviate nella lista, il rifiuto di compact, il job spento"

Da 2026.11.10 `openclaw sessions list --all-agents --json` elenca anche le
sessioni archiviate senza dirlo, e `sessions.compact` le rifiuta. Quali siano
lo dice il Gateway. Qui il Gateway è finto; il comportamento vero lo giudica
`test_nexgen_openclaw_contract_groom.py`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402
from nexgen_core.cli import openclaw_cmds  # noqa: E402
from nexgen_core.openclaw import sessions_archived, sessions_groom  # noqa: E402
from nexgen_core.openclaw.groom_policy import IDLE_SECONDS  # noqa: E402

NOW = 200_000.0 * 1000.0
ARCHIVED = "agent:capo:parity-vecchia"
LIVE = "agent:main:main"


@pytest.fixture(autouse=True)
def _english_lines():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _idle(key: str, tokens: int) -> dict:
    agent = key.split(":")[1]
    return {"key": key, "agentId": agent, "totalTokens": tokens, "updatedAt": NOW,
            "lastInteractionAt": NOW - (IDLE_SECONDS + 120) * 1000.0, "status": "done"}


class Gateway:
    """La CLI come la vede il groom: elenca tutto, dice a parte le archiviate e le rifiuta."""

    def __init__(self, sessions, archived=(), *, page=1000, broken=""):
        self.sessions = sessions
        self.archived = list(archived)
        self.page = page
        self.broken = broken
        self.compactions: list[str] = []

    def __call__(self, argv, **_kwargs):
        verb = argv[1] if argv[1] == "gateway" else argv[2]
        if verb == self.broken:
            return subprocess.CompletedProcess(argv, 1, "", "gateway closed")
        if verb == "list":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sessions": self.sessions}), "")
        if verb == "gateway":
            offset = json.loads(argv[argv.index("--params") + 1])["offset"]
            end = offset + self.page
            rows = [{"key": key} for key in self.archived[offset:end]]
            more = end < len(self.archived)
            answer = {"sessions": rows, "hasMore": more, "nextOffset": end if more else None}
            return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")
        self.compactions.append(argv[3])
        if argv[3] in self.archived or argv[3].endswith("refused"):
            refusal = {"ok": False, "error": {"message": f"Session {argv[3]} changed before compaction. Retry."}}
            return subprocess.CompletedProcess(argv, 1, json.dumps(refusal), "")
        return subprocess.CompletedProcess(argv, 0, '{"ok": true, "compacted": true}', "")


class Phone:
    def __init__(self):
        self.alerts: list[dict] = []

    def send_alert(self, **alert):
        self.alerts.append(alert)
        return True


def _groom(gateway, monkeypatch, home, phone=None):
    monkeypatch.setattr(sessions_groom.subprocess, "run", gateway)
    return sessions_groom.groom(openclaw="openclaw", now_ms=NOW, emit=lambda _line: None, home=home,
                                phone=phone)


def _command(gateway, monkeypatch, home) -> int:
    monkeypatch.setattr(sessions_groom.subprocess, "run", gateway)
    monkeypatch.setattr(sessions_groom, "_resolve_binary", lambda name="openclaw": "openclaw")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return openclaw_cmds.cmd_groom(Namespace(dry_run=False))


def test_an_archived_session_is_never_asked(monkeypatch, tmp_path):
    gateway = Gateway([_idle(ARCHIVED, 900_000), _idle(LIVE, 200_000)], [ARCHIVED])

    report = _groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == [LIVE], "la grande archiviata passava davanti e veniva rifiutata"
    assert report.failed == 0


def test_every_page_of_archived_sessions_is_read(monkeypatch):
    keys = [f"agent:capo:old-{number}" for number in range(5)]
    monkeypatch.setattr(sessions_groom.subprocess, "run", Gateway([], keys, page=2))

    assert sessions_archived.archived_keys("openclaw", sessions_groom._run) == set(keys)


@pytest.mark.parametrize("broken", ["list", "gateway"])
def test_a_listing_that_cannot_be_read_fails_the_run_instead_of_saying_nothing_to_do(
    broken, monkeypatch, tmp_path, capsys
):
    gateway = Gateway([_idle(LIVE, 200_000)], broken=broken)

    assert _command(gateway, monkeypatch, tmp_path) == 1
    assert gateway.compactions == []
    assert "gateway closed" in capsys.readouterr().err


def test_a_refused_chat_is_heard(monkeypatch, tmp_path):
    phone = Phone()

    report = _groom(Gateway([_idle("agent:main:refused", 900_000)]), monkeypatch, tmp_path, phone=phone)

    assert report.failed == 1 and len(phone.alerts) == 1


def test_a_refused_chat_never_fails_the_job(monkeypatch, tmp_path):
    """La classe del guasto, qualunque sia la prossima causa del rifiuto."""
    gateway = Gateway([_idle("agent:main:refused", 900_000)])

    assert _command(gateway, monkeypatch, tmp_path) == 0
    assert gateway.compactions == ["agent:main:refused"]
