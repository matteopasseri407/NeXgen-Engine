"""Test per l'event sink: cosa emette, per chi, e cosa si rifiuta di dire.

Il sink e' l'unico produttore degli eventi di ciclo di vita delle CLI. I suoi
difetti non danno errori: danno silenzio, o peggio fanno leggere ad alta voce
il JSON di un hook. Sono difetti che si scoprono solo usandolo, quindi vanno
fermati qui.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

SINK = Path(__file__).resolve().parents[1] / "hooks" / "nexgen-event-sink.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")


class Collector:
    """Un socket unix che raccoglie quello che il sink gli manda."""

    def __init__(self) -> None:
        # AF_UNIX ha un limite di 107 caratteri sul path: tmp corto, non tmp_path.
        self.dir = tempfile.mkdtemp(prefix="sink")
        self.path = os.path.join(self.dir, "e.sock")
        self.events: list[dict] = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(16)
        self._srv.settimeout(5)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except Exception:
                return
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            conn.close()
            for line in data.decode("utf-8", "replace").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))

    def env(self, *, vocal: bool = True, session: str = "voice-test1234", cli: str = "claude") -> dict:
        env = dict(os.environ, NEXGEN_EVENT_IPC_PATH=self.path, COCKPIT_CLI=cli)
        if vocal:
            env["COCKPIT_VOCALE"] = "1"
            env["COCKPIT_SESSION_ID"] = session
        else:
            env.pop("COCKPIT_VOCALE", None)
        return env

    def fire(self, event: str, cli: str, payload: dict, **kw) -> None:
        subprocess.run(
            ["node", str(SINK), event, cli],
            input=json.dumps(payload), text=True, env=self.env(cli=cli, **kw),
            timeout=15, capture_output=True,
        )
        time.sleep(0.35)

    def close(self) -> None:
        self._srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)


@pytest.fixture
def sink():
    c = Collector()
    yield c
    c.close()


def _transcript(sink: Collector, text: str, sidechain_after: str | None = None) -> str:
    rows = [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}]
    if sidechain_after:
        rows.append({"type": "assistant", "isSidechain": True,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": sidechain_after}]}})
    path = Path(sink.dir) / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


# --- l'identita' della sessione ------------------------------------------


@pytest.mark.parametrize("cli", ["claude", "codex", "antigravity"])
def test_the_cockpit_session_id_wins_over_the_cli_own_id(sink, cli) -> None:
    """Il consumatore filtra su COCKPIT_SESSION_ID.

    Lasciare vincere l'id interno della CLI non da' nessun errore: fa solo
    sembrare ogni evento come proveniente da una sessione che nessuno ha
    marcato vocale, e la risposta non viene mai detta.
    """
    tr = _transcript(sink, "La risposta del turno.")
    sink.fire("on_done", cli, {"session_id": "id-interno-della-cli", "transcript_path": tr,
                               "hook_event_name": "Stop"}, session=f"voice-{cli[:4]}0001")
    assert len(sink.events) == 1
    assert sink.events[0]["session_id"] == f"voice-{cli[:4]}0001"
    assert sink.events[0]["cli"] == cli


def test_a_session_nobody_marked_vocal_stays_silent(sink) -> None:
    tr = _transcript(sink, "Non deve uscire.")
    sink.fire("on_done", "claude", {"transcript_path": tr, "hook_event_name": "Stop"}, vocal=False)
    assert sink.events == []


# --- cosa viene detto ----------------------------------------------------


def test_the_reply_is_read_from_the_transcript(sink) -> None:
    """Lo Stop hook di Claude non contiene la risposta, solo un percorso."""
    tr = _transcript(sink, "Ho sistemato la soglia.")
    sink.fire("on_done", "claude", {"transcript_path": tr, "hook_event_name": "Stop"})
    assert sink.events[0]["text"] == "Ho sistemato la soglia."


def test_subagent_turns_are_not_the_reply(sink) -> None:
    """I turni dei subagenti stanno nello stesso transcript."""
    tr = _transcript(sink, "La vera risposta.", sidechain_after="LAVORO DI UN SUBAGENTE")
    sink.fire("on_done", "claude", {"transcript_path": tr, "hook_event_name": "Stop"})
    assert sink.events[0]["text"] == "La vera risposta."


def test_the_hook_envelope_is_never_emitted_as_text(sink) -> None:
    """Restituire il buffer grezzo faceva leggere ad alta voce il JSON
    dell'hook, con dentro i comandi bash."""
    sink.fire("on_done", "claude", {"hook_event_name": "Stop", "cwd": "/x",
                                    "transcript_path": "/non/esiste.jsonl"})
    assert all("hook_event_name" not in e["text"] for e in sink.events)


def test_a_tool_call_with_no_prose_says_nothing(sink) -> None:
    sink.fire("on_step", "claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                    "tool_input": {"command": "rm -rf /"}})
    assert sink.events == []


def test_the_end_of_a_silent_turn_is_still_reported(sink) -> None:
    """Un consumatore deve poter distinguere 'finito senza dire niente' da
    'sta ancora lavorando'."""
    sink.fire("on_done", "claude", {"hook_event_name": "Stop", "transcript_path": "/non/esiste.jsonl"})
    assert len(sink.events) == 1 and sink.events[0]["text"] == ""


def test_an_explicit_reply_field_is_used_as_is(sink) -> None:
    sink.fire("on_done", "antigravity", {"last_assistant_message": "Detto direttamente."})
    assert sink.events[0]["text"] == "Detto direttamente."


# --- il ramo plugin di OpenCode -------------------------------------------


def test_the_opencode_plugin_path_obeys_the_same_rules(sink) -> None:
    driver = Path(sink.dir) / "drive.mjs"
    driver.write_text(
        f'import plugin from "{SINK}";\n'
        'const h = await plugin({});\n'
        'await h["session.idle"]({ lastAssistantMessage: "Da OpenCode.", sessionID: "id-interno" });\n'
        'await new Promise(r => setTimeout(r, 300));\n',
        encoding="utf-8",
    )
    subprocess.run(["node", str(driver)], env=sink.env(session="voice-oc000001", cli="opencode"),
                   timeout=15, capture_output=True)
    time.sleep(0.35)
    assert len(sink.events) == 1
    assert sink.events[0]["session_id"] == "voice-oc000001"
    assert sink.events[0]["text"] == "Da OpenCode."


def test_no_socket_means_no_work(sink) -> None:
    """Nessun consumatore in ascolto: uscita immediata, senza errori."""
    env = sink.env()
    env["NEXGEN_EVENT_IPC_PATH"] = os.path.join(sink.dir, "non-esiste.sock")
    proc = subprocess.run(["node", str(SINK), "on_done", "claude"],
                          input='{"hook_event_name":"Stop"}', text=True, env=env,
                          timeout=15, capture_output=True)
    assert proc.returncode == 0
