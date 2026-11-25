"""Ogni harness sa partire senza finestra, e `sup run` parte così se non gli si chiede `--tab`.

Nessun harness vero: gli argv sono costruiti dagli adattatori e il processo, dove
serve, è un `Popen` finto che registra la riga di comando.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import supervision_adapters as registry
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_turn_launch as launch
from nexgen_local.cmds import sup as sup_cmd
from nexgen_local.supervision_adapters import base


def test_every_harness_has_a_headless_first_turn():
    """Il default di `sup run` è headless: un harness che non lo sa fare lo direbbe solo a metà lavoro."""
    missing = [adapter.name for adapter in registry.adapters() if not adapter.capabilities.has("headless")]
    assert missing == []


@pytest.mark.parametrize("name, expected", [
    ("opencode", ["opencode", "run", "-m", "opencode-go/deepseek-v4.1-flash", "punta al brief"]),
    ("claude", ["claude", "-p", "--session-id", "id-1", "--model", "opencode-go/deepseek-v4.1-flash",
                "--permission-mode", "acceptEdits", "punta al brief"]),
    ("agy", ["agy", "--mode", "accept-edits", "--model", "opencode-go/deepseek-v4.1-flash",
             "--print", "punta al brief"]),
])
def test_the_headless_command_carries_the_model_and_ends_on_the_pointer(tmp_path, name, expected):
    adapter = registry.get(name)
    argv = adapter.headless_argv(name, tmp_path, "punta al brief", session="id-1",
                                 model="opencode-go/deepseek-v4.1-flash")
    assert argv == expected


def test_codex_opens_its_first_thread_with_exec_in_the_copy(tmp_path):
    copy = tmp_path / "23-unita-prova"
    copy.mkdir()
    argv = registry.get("codex").headless_argv("codex", copy, "punta al brief")
    assert argv[:4] == ["codex", "exec", "-C", str(copy)]
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path), "la cartella del compito resta scrivibile"
    assert argv[-1] == "punta al brief"


def test_agy_puts_the_prompt_right_after_print_also_on_a_later_turn():
    """`--print` prende il prompt come valore: qualsiasi flag in mezzo diventa il prompt e agy esce con 2."""
    argv = registry.get("agy").send_argv("agy", "conv-1", "correzioni")
    assert argv[-2:] == ["--print", "correzioni"]
    assert argv[argv.index("--conversation") + 1] == "conv-1"


def test_the_first_turn_uses_the_headless_form_not_a_resume(tmp_path, monkeypatch):
    """Claude riceve `-p --session-id`, non `--resume` su una sessione che ancora non esiste."""
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            self.pid = 4321

    monkeypatch.setattr(launch.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(registry.get("claude"), "locate", lambda: "claude")
    copy = tmp_path / "23-unita-prova"
    copy.mkdir()

    runner = harness._runner_for(registry.get("claude"), first=True)
    runner("id-nuovo", "punta al brief", copy, "")

    assert "--session-id" in seen["argv"] and "--resume" not in seen["argv"]
    assert "-p" in seen["argv"]


def test_a_harness_without_a_headless_form_is_refused_before_anything_starts(tmp_path, monkeypatch):
    class Windowed(base.Adapter):
        name = "finto"
        executable = "finto"
        capabilities = base.Capabilities(open_tab=True, window=True, headless=False)

    monkeypatch.setattr(harness, "_executable_for", lambda adapter: "finto")
    with pytest.raises(harness.HarnessError, match="--tab"):
        harness._open_first_tab(
            "compito", "prova", tmp_path, "prompt", lambda *a, **k: pytest.fail("nessuna finestra"),
            None, 0.0, adapter=Windowed(), headless=True,
            runner=lambda *a: pytest.fail("nessun turno"),
        )


@pytest.mark.parametrize("flags, headless", [([], True), (["--headless"], True), (["--tab"], False)])
def test_sup_run_is_headless_unless_the_owner_asks_for_a_tab(monkeypatch, flags, headless):
    seen = {}
    monkeypatch.setattr(sup_cmd.harness, "run_unit", lambda task, unit, **options: seen.update(options) or {})
    parser = argparse.ArgumentParser()
    sup_cmd._register_run(parser)
    sup_cmd._run_unit(parser.parse_args(["compito", "prova", "-m", "opencode-go/x", *flags]))
    assert seen.get("headless", False) is headless
    assert seen["model"] == "opencode-go/x", "il modello viaggia anche sul turno headless"


def test_a_turn_is_handed_to_its_guardian_with_the_lane_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(launch.jobs, "adopt", lambda name, pid: "")
    monkeypatch.setattr(launch.policy, "turn_timeout_minutes", lambda: 30.0)
    monkeypatch.setattr(launch.reaper, "watchable", lambda proc: True)
    monkeypatch.setattr(launch.reaper, "watch", lambda task, unit, proc, **kw: seen.update(kw, unit=unit) or "")
    launch.hold_turn("compito", "prova", SimpleNamespace(pid=7))
    assert seen["timeout_s"] == 1800.0
    assert seen["unit"] == "prova"


def test_a_turn_the_guardian_cannot_watch_is_journalled(monkeypatch):
    events = []
    monkeypatch.setattr(launch.jobs, "adopt", lambda name, pid: "")
    monkeypatch.setattr(launch.policy, "turn_timeout_minutes", lambda: 30.0)
    monkeypatch.setattr(launch.reaper, "watchable", lambda proc: True)
    monkeypatch.setattr(launch.reaper, "watch", lambda *a, **k: "python non trovato")
    monkeypatch.setattr(launch.state, "append_event", lambda task, event, **f: events.append((event, f)))
    launch.hold_turn("compito", "prova", SimpleNamespace(pid=7))
    assert events == [("turn_unguarded", {"unit": "prova", "pid": 7, "reason": "python non trovato"})]


def test_a_process_the_engine_did_not_start_is_never_handed_to_a_guardian(monkeypatch):
    """Il finto processo di un test, o un pid che non è un `Popen`, non merita un guardiano."""
    watched = []
    monkeypatch.setattr(launch.jobs, "adopt", lambda name, pid: "")
    monkeypatch.setattr(
        launch.reaper, "watch", lambda *a, **k: watched.append(a) or "mai chiamato",
    )

    launch.hold_turn("compito", "prova", SimpleNamespace(pid=7))

    assert watched == []


def test_the_launch_passes_the_adapter_environment_only_when_it_adds_one(tmp_path, monkeypatch):
    seen = {}

    class Adapter(base.Adapter):
        name = "finto"

        def send_argv(self, executable, session, prompt, *, model=""):
            return [executable, prompt]

        def turn_env(self, model=""):
            return {"CHIAVE_FINTA": f"per {model}"}

    monkeypatch.setattr(launch.subprocess, "Popen", lambda argv, **kw: seen.update(kw) or SimpleNamespace(pid=1))
    copy = tmp_path / "23-unita-prova"
    copy.mkdir()
    launch.run_turn(Adapter(), "finto", "s", "p", copy, "m/x")
    assert seen["env"]["CHIAVE_FINTA"] == "per m/x"
    assert seen["stdin"] is subprocess.DEVNULL


def test_every_turn_names_its_copy_in_pwd_not_the_shell_it_came_from(tmp_path, monkeypatch):
    """OpenCode sceglie la cartella della sessione da `PWD`: un `sup run` da un altro checkout lavorava lì."""
    seen = {}

    class Adapter(base.Adapter):
        name = "finto"

        def send_argv(self, executable, session, prompt, *, model=""):
            return [executable, prompt]

    monkeypatch.setenv("PWD", str(tmp_path / "un-altro-checkout"))
    monkeypatch.setattr(launch.subprocess, "Popen", lambda argv, **kw: seen.update(kw) or SimpleNamespace(pid=1))
    copy = tmp_path / "23-unita-prova"
    copy.mkdir()
    launch.run_turn(Adapter(), "finto", "s", "p", copy)
    assert seen["env"]["PWD"] == str(copy)
    assert seen["cwd"] == copy


def test_the_stderr_log_sits_in_the_task_folder(tmp_path):
    assert launch.stderr_log(tmp_path / "23-unita-prova", "ses/1") == tmp_path / "60-stderr-ses-1.log"
    assert isinstance(harness.stderr_log(Path("x/y"), "s"), Path)


@pytest.mark.parametrize("asked, expected", [(False, False), (True, True), (None, True)])
def test_a_lost_session_reopens_the_way_the_owner_asked_on_this_run(tmp_path, monkeypatch, asked, expected):
    """`sup run --tab` vince sul diario; senza scelta (un giro di revisione) vale come era partita l'unità."""
    seen = {}

    def lost(*a, **k):
        raise harness.SessionLost("ses_x", "sparita")

    monkeypatch.setattr(harness, "_enter_the_session", lost)
    monkeypatch.setattr(harness.state, "append_event", lambda *a, **k: None)
    monkeypatch.setattr(harness, "_ran_in_tab", lambda task, unit: False)
    monkeypatch.setattr(harness, "_open_first_tab", lambda *a, **k: seen.update(k) or {"mode": "x"})

    harness._deliver(
        "compito", "prova", tmp_path, "p", "ses_x", event="unit_started", extra={},
        opener=None, runner=None, lister=None, measure=None, wait_s=0,
        adapter=registry.get("opencode"), headless=asked,
    )

    assert seen["headless"] is expected
