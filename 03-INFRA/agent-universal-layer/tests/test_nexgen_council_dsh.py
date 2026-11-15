"""Il seggio DeepSeek Harness (dsh) del Council, senza chiamate live.

Le fixture sono l'output NDJSON di un run live registrato il 2026-11-14 con DeepSeek Harness
0.2.2-rc.3 su Windows, stessi argv del seggio: profilo headless, patch per chiamata, preset
read-only, approvazioni negate, prompt su stdin. Nessun test avvia un modello.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import seat_dsh  # noqa: E402
import seat_process  # noqa: E402
from seat_dsh import DshSeatError, parse_dsh_result  # noqa: E402
from seat_process import SeatRunError, _build_seat_command, _effort_forwarding, run_seat  # noqa: E402

#: Un run live riuscito: risposta "pong", 7940 token in un passo, nessun costo riportato.
LIVE_ANSWER = """\
{"type":"session","sessionId":"session-5bf63610-c113-4e6f-befb-1b82627f5765","cwd":"C:\\\\Temp\\\\work"}
{"type":"status","phase":"turn_start","turn":1}
{"type":"status","phase":"step_start","turn":1,"step":1}
{"type":"text","text":"pong"}
{"type":"status","phase":"step_end","turn":1,"step":1,"usage":{"inputTokens":7936,"outputTokens":4,"totalTokens":7940}}
{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}
{"type":"final","sessionId":"session-5bf63610-c113-4e6f-befb-1b82627f5765","reason":{"kind":"completed"},"text":"pong"}
"""

SEAT = {"cli": "dsh", "model": "opencode-go/deepseek-v4.1-flash", "reasoning_effort": "high"}


def _patch_document(invocation) -> list[dict]:
    patch = Path(invocation.argv[invocation.argv.index("--patch") + 1])
    return yaml.safe_load(patch.read_text(encoding="utf-8"))


def test_argv_is_the_headless_json_run_with_the_prompt_off_argv(tmp_path):
    invocation = _build_seat_command(SEAT, "il brief privato", tmp_path)
    argv = invocation.argv
    assert argv[1:] == [
        "--profile", "headless",
        "--patch", argv[4],
        "--permission-mode", "read-only",
        "--approval-policy", "never",
        "--json", "-",
    ]
    # Il brief viaggia su stdin: argv e' visibile nella tabella dei processi.
    assert invocation.stdin_text == "il brief privato"
    assert "il brief privato" not in " ".join(invocation.argv)
    assert invocation.cwd == tmp_path
    assert invocation.output_file is None and invocation.input_file is None


def test_the_seat_runs_on_an_isolated_harness_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "plan-key-that-must-not-be-printed")
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    home = Path(invocation.env["DSH_HOME"])
    assert home.parent == invocation.scratch
    assert home.is_dir()
    # La chiave del piano Go arriva all'harness col nome che legge lui.
    assert invocation.env["OPENCODE_API_KEY"] == "plan-key-that-must-not-be-printed"
    assert "OPENCODE_GO_API_KEY" not in invocation.env


def test_the_harness_own_key_is_bridged_when_that_is_the_only_one_exported(tmp_path, monkeypatch):
    """Un host grafico puo' avere solo `OPENCODE_API_KEY`, l'unico nome che l'harness legge."""
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_API_KEY", "harness-key")
    monkeypatch.setattr("nexgen_core.deposit_env.read_deposit", lambda: {})
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    assert invocation.env["OPENCODE_API_KEY"] == "harness-key"


def test_the_plan_key_in_the_secrets_deposit_reaches_the_seat(tmp_path, monkeypatch):
    """Nessun launcher carica il deposito: la seduta lo legge invece di morire senza chiave."""
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr(
        "nexgen_core.deposit_env.read_deposit", lambda: {"OPENCODE_GO_API_KEY": "deposit-key"},
    )
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    assert invocation.env["OPENCODE_API_KEY"] == "deposit-key"


def test_the_plan_key_wins_over_the_harness_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "plan-key")
    monkeypatch.setenv("OPENCODE_API_KEY", "harness-key")
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    assert invocation.env["OPENCODE_API_KEY"] == "plan-key"


def test_a_route_with_no_key_anywhere_bridges_nothing(tmp_path, monkeypatch):
    """Un ponte vuoto sarebbe una credenziale falsa: meglio il MISSING_CREDENTIAL dell'harness."""
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr("nexgen_core.deposit_env.read_deposit", lambda: {})
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    assert "OPENCODE_API_KEY" not in invocation.env


def test_the_patch_pins_the_route_and_turns_the_three_uploads_off(tmp_path):
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    data = invocation.scratch / "dsh-data"
    assert _patch_document(invocation) == [
        {"id": "llm-pi-ai", "config": {"providers": {"opencode-go": {"apiKeyEnv": "OPENCODE_API_KEY"}}}},
        {
            "id": "agent-default-model",
            "config": {"provider": "opencode-go", "model": "deepseek-v4.1-flash", "reasoningEffort": "high"},
        },
        # La sessione e lo store delle proiezioni finiscono nella directory privata del seggio.
        {"id": "session-persistence-jsonl", "config": {"root": str(data / "sessions")}},
        {"id": "storage-json", "config": {"root": str(data / "storages")}},
        {"id": "session-log-deepseek", "config": {"enabled": False}},
        {"id": "session-telemetry-otel", "config": {"mode": "DISABLED"}},
        {"id": "plugin-package-inventory-deepseek", "config": {"enabled": False}},
    ]


@pytest.mark.parametrize("effort", [None, "none", ""])
def test_no_effort_leaves_the_patch_without_a_reasoning_level(tmp_path, effort):
    seat = {"cli": "dsh", "model": "opencode-go/deepseek-v4.1-flash"}
    if effort is not None:
        seat["reasoning_effort"] = effort
    selection = _patch_document(_build_seat_command(seat, "brief", tmp_path))[1]["config"]
    assert selection == {"provider": "opencode-go", "model": "deepseek-v4.1-flash"}

def test_the_patch_is_written_inside_the_private_scratch_directory(tmp_path):
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    patch = Path(invocation.argv[invocation.argv.index("--patch") + 1])
    assert patch.parent == invocation.scratch
    assert patch.is_file()


def test_a_model_without_a_provider_route_is_refused_before_any_process(tmp_path):
    with pytest.raises(SeatRunError) as error:
        _build_seat_command({"cli": "dsh", "model": "deepseek-v4.1-flash"}, "brief", tmp_path)
    assert error.value.kind == "invocation"
    assert "<provider>/<model>" in str(error.value)
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_the_effort_is_shown_to_the_person_and_carried_by_the_patch(tmp_path):
    args, label = _effort_forwarding(SEAT)
    assert args == []
    assert label == ", effort high"


def test_the_live_answer_is_read_from_the_final_event():
    answer, usage = parse_dsh_result(LIVE_ANSWER, "opencode-go/deepseek-v4.1-flash", {})
    assert answer == "pong"
    # L'harness non registra alcun costo: il riepilogo stampa solo i token.
    assert usage == {"tokens": 7940}


def test_a_thinking_stream_stays_out_of_the_answer():
    raw = LIVE_ANSWER.replace(
        '{"type":"status","phase":"step_start","turn":1,"step":1}',
        '{"type":"status","phase":"step_start","turn":1,"step":1}\n'
        '{"type":"thinking","text":"l\'utente vuole solo pong"}',
    )
    answer, _usage = parse_dsh_result(raw, "m", {})
    assert answer == "pong"


def test_a_turn_that_ended_in_error_is_a_seat_error_with_the_harness_message():
    raw = (
        '{"type":"session","sessionId":"session-1","cwd":"C:\\\\work"}\n'
        '{"type":"status","phase":"step_end","turn":1,"step":1}\n'
        '{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"error",'
        '"error":{"message":"no adapter registered for provider \\"opencode-go\\"","code":"NO_ADAPTER"}}}\n'
        '{"type":"final","sessionId":"session-1","reason":{"kind":"error","error":'
        '{"message":"no adapter registered for provider \\"opencode-go\\"","code":"NO_ADAPTER"}},"text":""}\n'
    )
    with pytest.raises(DshSeatError) as error:
        parse_dsh_result(raw, "m", {})
    assert error.value.kind == "seat_error"
    assert "no adapter registered for provider" in str(error.value)


def test_a_stream_cut_before_the_final_event_is_not_an_empty_opinion():
    raw = LIVE_ANSWER.split('{"type":"status","phase":"turn_end"')[0]
    with pytest.raises(DshSeatError) as error:
        parse_dsh_result(raw, "m", {})
    assert error.value.kind == "invalid_output"
    assert "final answer" in str(error.value)


def test_output_that_is_not_the_harness_stream_is_refused():
    with pytest.raises(DshSeatError) as error:
        parse_dsh_result("pong\n", "m", {})
    assert error.value.kind == "invalid_output"


def test_a_completed_turn_with_no_text_is_an_empty_response():
    raw = '{"type":"final","sessionId":"s","reason":{"kind":"completed"},"text":"   "}\n'
    with pytest.raises(DshSeatError) as error:
        parse_dsh_result(raw, "m", {})
    assert error.value.kind == "empty_response"


def test_the_launcher_is_found_on_path_or_in_the_windows_install(monkeypatch, tmp_path):
    monkeypatch.setattr(seat_dsh.shutil, "which", lambda name: r"C:\npm\dsh.cmd")
    assert seat_dsh.dsh_executable() == r"C:\npm\dsh.cmd"

    monkeypatch.setattr(seat_dsh.shutil, "which", lambda name: None)
    monkeypatch.setattr(seat_dsh.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert seat_dsh.dsh_executable() is None
    installed = tmp_path / seat_dsh.WINDOWS_LAUNCHER
    installed.parent.mkdir(parents=True)
    installed.write_text("@echo off\n", encoding="utf-8")
    assert seat_dsh.dsh_executable() == str(installed)


def test_a_host_without_the_harness_still_builds_the_command(tmp_path, monkeypatch):
    """Il probe dei flag non deve dipendere da un'installazione presente."""
    monkeypatch.setattr(seat_dsh, "dsh_executable", lambda: None)
    argv = _build_seat_command(SEAT, "brief", tmp_path).argv
    assert argv[0] == "dsh"


@pytest.mark.skipif(seat_dsh.os.name != "nt", reason="the wrapper exists for the Windows cmd.exe road")
def test_a_batch_launcher_under_a_path_with_a_space_is_wrapped(tmp_path, monkeypatch):
    """cmd.exe, l'unico modo per avviare un .cmd su Windows, spezza il percorso al primo spazio."""
    launcher = r"C:\Program Files\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd"
    monkeypatch.setattr(seat_dsh.shutil, "which", lambda name: launcher)
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    wrapper = Path(invocation.argv[0])
    assert " " not in str(wrapper)
    assert wrapper.parent == invocation.scratch
    assert wrapper.read_text(encoding="utf-8") == f'@call "{launcher}" %*\n'
    assert invocation.argv[1:] == [
        "--profile", "headless",
        "--patch", invocation.argv[4],
        "--permission-mode", "read-only",
        "--approval-policy", "never",
        "--json", "-",
    ]


def test_a_launcher_without_a_space_is_started_directly(tmp_path, monkeypatch):
    launcher = r"C:\npm\dsh.cmd"
    monkeypatch.setattr(seat_dsh.shutil, "which", lambda name: launcher)
    invocation = _build_seat_command(SEAT, "brief", tmp_path)
    assert invocation.argv[0] == launcher


def test_run_seat_returns_the_answer_and_the_tokens_of_a_dsh_stream(tmp_path, monkeypatch):
    """Il percorso intero: ambiente isolato, patch su disco, spawn, stream NDJSON, lettura."""
    script = tmp_path / "fake-dsh.py"
    script.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        f"sys.stdout.write({LIVE_ANSWER!r})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        seat_process, "dsh_argv",
        lambda patch, scratch: [sys.executable, "-u", str(script), "--patch", str(patch)],
    )
    answer, usage = run_seat(dict(SEAT), "Reply with the word pong only.", tmp_path, 30)
    assert answer == "pong"
    assert usage["tokens"] == 7940
    # L'harness non riporta alcun costo: il seggio non ne inventa uno.
    assert "cost" not in usage


def test_run_seat_reports_an_error_turn_as_a_failed_seat(tmp_path, monkeypatch):
    script = tmp_path / "fake-dsh-error.py"
    raw = (
        '{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"error",'
        '"error":{"message":"MISSING_CREDENTIAL: no credential for provider route","code":"MISSING_CREDENTIAL"}}}\n'
        '{"type":"final","sessionId":"s","reason":{"kind":"error","error":'
        '{"message":"MISSING_CREDENTIAL: no credential for provider route","code":"MISSING_CREDENTIAL"}},"text":""}\n'
    )
    script.write_text(
        "import sys\nsys.stdin.read()\n" f"sys.stdout.write({raw!r})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        seat_process, "dsh_argv",
        lambda patch, scratch: [sys.executable, "-u", str(script), "--patch", str(patch)],
    )
    with pytest.raises(SeatRunError) as error:
        run_seat(dict(SEAT), "brief", tmp_path, 30)
    assert error.value.kind == "seat_error"
    assert "MISSING_CREDENTIAL" in str(error.value)


def test_the_contract_probe_lists_the_headless_flags():
    """`council contract` interroga il profilo headless, non l'help di primo livello."""
    import cli_contract

    assert cli_contract.HELP_ARGV["dsh"] == ["dsh", "--profile", "headless", "--help"]
    flags = cli_contract.flags_passed("dsh")
    assert flags == ["--approval-policy", "--json", "--patch", "--permission-mode", "--profile"]


def test_the_contract_probe_reads_both_help_pages(monkeypatch):
    """--patch e --profile stanno nell'help del launcher, il resto in quello del profilo headless."""
    import cli_contract

    monkeypatch.setattr(cli_contract, "dsh_executable", lambda: "dsh")
    monkeypatch.setattr(cli_contract, "dsh_launcher", lambda scratch: "dsh")
    seen: list[list[str]] = []

    def probe(argv):
        seen.append(argv)
        return True, "Usage:\n--patch\n--profile\n--json\n--permission-mode\n--approval-policy"

    result = cli_contract.check_cli("dsh", probe)
    assert result.status == "ok"
    assert [argv[1:] for argv in seen] == [["--help"], ["--profile", "headless", "--help"]]


def test_a_dsh_seat_spends_the_pool_its_route_names():
    """La CLI non decide il canale: un seggio dsh su opencode-go spende la quota Go."""
    from routing import seat_channel

    assert seat_channel({"cli": "dsh", "model": "opencode-go/deepseek-v4.1-flash"}) == "go"
    assert seat_channel({"cli": "dsh", "model": "opencode/qwen3-coder-free"}) == "zen-free"
    assert seat_channel({"cli": "dsh", "model": "local/nome-modello"}) is None


def test_a_dsh_seat_is_available_only_where_its_launcher_exists(monkeypatch):
    import routing

    seat = {"vendor": "deepseek", "cli": "dsh", "model": "opencode-go/deepseek-v4.1-flash"}
    monkeypatch.setattr(routing, "_run_probe", lambda argv: (True, "--profile --patch --json"))
    monkeypatch.setattr(routing, "dsh_executable", lambda: r"C:\Programs\DeepSeek Harness\dsh.cmd")
    available = routing.seat_capabilities({"seggio": seat})["seggio"]
    assert available.available is True
    assert "per-call patch" in available.reason

    monkeypatch.setattr(routing, "dsh_executable", lambda: None)
    missing = routing.seat_capabilities({"seggio": seat})["seggio"]
    assert missing.available is False
    assert "not present on this host" in missing.reason
