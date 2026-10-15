"""Pinning dello split runtimes/base prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con config finte:
sink_command finto stabile, sidecar scritto solo se cambia, firme pubbliche
invariate. Solo config finte in tmp_path, mai config vere, mai rete.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.runtimes.base import (  # noqa: E402
    EVENT_SINK_NAME,
    Runtime,
)


class _FakeRuntime(Runtime):
    name = "fake"

    def is_installed(self, home: Path) -> bool:
        del home
        return True

    def read_posture(self, home: Path) -> str | None:
        del home
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        del home, posture
        return None

    def install_event_sink(self, home: Path, sink_source: Path) -> str | None:
        del home, sink_source
        return None

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        del home, hook_source, engine_hooks_dir
        return None


def _sink_cmd(tmp_path: Path) -> str:
    return f'node "{tmp_path / EVENT_SINK_NAME}"'


def test_sink_command_finto_stabile() -> None:
    hooks = {
        "Stop": [
            {"hooks": [{"command": _sink_cmd(Path("/tmp/fake"))}, {"command": "node other.mjs"}]},
            {"hooks": [{"command": "node keep.mjs"}]},
        ],
        "PreToolUse": [
            {"hooks": [{"command": 123}, {"command": ""}]},
            {"no-hooks-here": True},
        ],
        "SessionStart": [{"hooks": [{"command": _sink_cmd(Path("/tmp/fake"))}]}],
    }
    changed = Runtime.strip_event_sink_hooks(hooks)
    assert changed is True
    # Stop: il gruppo sink perde solo la voce sink, l'altro comando resta.
    assert hooks["Stop"][0] == {"hooks": [{"command": "node other.mjs"}]}
    assert hooks["Stop"][1] == {"hooks": [{"command": "node keep.mjs"}]}
    # PreToolUse: comandi non-stringa o vuoti non sono sink, tutto resta.
    assert hooks["PreToolUse"] == [
        {"hooks": [{"command": 123}, {"command": ""}]},
        {"no-hooks-here": True},
    ]
    # SessionStart non e' negli eventi di default: mai toccato.
    assert len(hooks["SessionStart"]) == 1
    # Seconda passata stabile: niente piu' da togliere.
    assert Runtime.strip_event_sink_hooks(hooks) is False


def test_sink_command_basename_e_gruppi_vuoti(tmp_path: Path) -> None:
    # Basename con path quotato e spazi: riconosciuto come sink dove la
    # piattaforma usa backslash come separatore, ignorato altrove.
    quoted = f'node "C:\\Program Files\\{EVENT_SINK_NAME}"'
    hooks: dict = {"Stop": [{"hooks": [{"command": quoted}]}]}
    if os.name == "nt":
        assert Runtime.strip_event_sink_hooks(hooks) is True
        assert "Stop" not in hooks  # evento rimasto senza gruppi: rimosso.
    else:
        assert Runtime.strip_event_sink_hooks(hooks) is False
        assert hooks["Stop"][0]["hooks"] == [{"command": quoted}]

    # Comando malformato (quote non chiusa): fallback senza crash, nessun falso positivo.
    hooks2: dict = {"Stop": [{"hooks": [{"command": 'node "unterminated'}]}]}
    assert Runtime.strip_event_sink_hooks(hooks2) is False
    assert hooks2["Stop"][0]["hooks"] == [{"command": 'node "unterminated'}]

    # Evento esplicito: SessionStart toccato solo se richiesto.
    sink = _sink_cmd(tmp_path)
    hooks3: dict = {"SessionStart": [{"hooks": [{"command": sink}]}]}
    assert Runtime.strip_event_sink_hooks(hooks3) is False
    assert Runtime.strip_event_sink_hooks(hooks3, events=("SessionStart",)) is True
    assert "SessionStart" not in hooks3

    # Gruppi senza lista hooks e eventi non-lista: mai toccati, mai crash.
    hooks4: dict = {"Stop": [{"altro": 1}, "stringa"], "PreToolUse": "non-una-lista"}
    assert Runtime.strip_event_sink_hooks(hooks4) is False
    assert hooks4 == {"Stop": [{"altro": 1}, "stringa"], "PreToolUse": "non-una-lista"}


def test_sidecar_scritto_solo_se_cambia(tmp_path: Path) -> None:
    rt = _FakeRuntime()
    home = tmp_path / "home"
    home.mkdir()
    body = tmp_path / "body.mjs"
    body.write_text("// policy\n", encoding="utf-8")
    sidecar = tmp_path / "sidecar.json"

    assert rt.write_guardrail_sidecar(sidecar, body=body, home=home) is True
    first = sidecar.read_text(encoding="utf-8")
    data = json.loads(first)
    assert data["hooks"] == [{"file": str(body), "timeout": 5}]
    assert data["strict"] is False
    assert data["autoAllow"] is False
    assert data["auditFile"].endswith("fake.json")

    # Identico: nessuna riscrittura.
    assert rt.write_guardrail_sidecar(sidecar, body=body, home=home) is False
    assert sidecar.read_text(encoding="utf-8") == first

    # Timeout diverso: riscrive.
    assert rt.write_guardrail_sidecar(sidecar, body=body, home=home, timeout=9) is True
    assert json.loads(sidecar.read_text(encoding="utf-8"))["hooks"] == [
        {"file": str(body), "timeout": 9}
    ]
    assert rt.write_guardrail_sidecar(sidecar, body=body, home=home, timeout=9) is False

    # strict/autoAllow assenti restano False; se dati, restano dopo (postura applicata dopo).
    assert rt.write_guardrail_sidecar(sidecar, body=body, home=home, timeout=9, strict=True) is True
    assert json.loads(sidecar.read_text(encoding="utf-8"))["strict"] is True
    assert rt.write_guardrail_sidecar(sidecar, body=body, home=home, timeout=9) is False
    assert json.loads(sidecar.read_text(encoding="utf-8"))["strict"] is True


def test_sidecar_flags_e_deploy_bytes_idempotenti(tmp_path: Path) -> None:
    rt = _FakeRuntime()
    home = tmp_path / "home"
    home.mkdir()
    body = tmp_path / "body.mjs"
    body.write_text("// policy\n", encoding="utf-8")
    sidecar = tmp_path / "sidecar.json"

    # Senza sidecar: niente da cambiare.
    assert rt.set_guardrail_flags(sidecar, strict=True) is False
    # Sidecar malformato: letto come {} -> niente da cambiare, mai crash.
    sidecar.write_text("non-json", encoding="utf-8")
    assert Runtime.read_guardrail_sidecar(sidecar) == {}
    assert rt.set_guardrail_flags(sidecar, strict=True) is False

    assert rt.write_guardrail_sidecar(sidecar, body=body, home=home) is True
    # Stessi valori: nessuna riscrittura.
    current = json.loads(sidecar.read_text(encoding="utf-8"))
    assert rt.set_guardrail_flags(sidecar, strict=current["strict"]) is False
    # Valore diverso: riscrive solo quella chiave.
    assert rt.set_guardrail_flags(sidecar, strict=True) is True
    assert json.loads(sidecar.read_text(encoding="utf-8"))["strict"] is True

    # deploy_bytes: identico -> False, diverso -> True.
    dst = tmp_path / "hook.mjs"
    assert Runtime.deploy_bytes(dst, b"// v1") is True
    assert Runtime.deploy_bytes(dst, b"// v1") is False
    assert Runtime.deploy_bytes(dst, b"// v2") is True
    assert dst.read_bytes() == b"// v2"

    # remove_deployed: True se c'era, False se mancava.
    assert Runtime.remove_deployed(dst) is True
    assert not dst.exists()
    assert Runtime.remove_deployed(dst) is False


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(Runtime.strip_event_sink_hooks).parameters) == [
        "hooks",
        "events",
    ]
    assert list(inspect.signature(Runtime.write_guardrail_sidecar).parameters) == [
        "self",
        "path",
        "body",
        "home",
        "timeout",
        "strict",
        "auto_allow",
    ]
    assert list(inspect.signature(Runtime.read_guardrail_sidecar).parameters) == ["path"]
    assert list(inspect.signature(Runtime.set_guardrail_flags).parameters) == [
        "self",
        "path",
        "flags",
    ]
    assert list(inspect.signature(Runtime.deploy_bytes).parameters) == ["dst", "body"]
    assert list(inspect.signature(Runtime.deploy_guardrail_core).parameters) == [
        "self",
        "directory",
        "engine_hooks_dir",
    ]
    assert list(inspect.signature(Runtime.remove_deployed).parameters) == ["path"]
    assert list(inspect.signature(Runtime.guardrail_sidecar).parameters) == ["self", "home"]
    # Default pubblici: cambiarli e' un cambio di contratto.
    assert Runtime.strip_event_sink_hooks.__defaults__ == (("Stop", "PreToolUse"),)
    sig = inspect.signature(Runtime.write_guardrail_sidecar)
    assert sig.parameters["timeout"].default == 5
    assert sig.parameters["strict"].default is None
    assert sig.parameters["auto_allow"].default is None
