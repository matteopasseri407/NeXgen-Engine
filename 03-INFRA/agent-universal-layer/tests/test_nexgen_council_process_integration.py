"""Exercise real pipes, subprocess exits and descendant cleanup, offline."""
from __future__ import annotations

import concurrent.futures
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import seat_process  # noqa: E402
import session  # noqa: E402
from seat_process import SeatInvocation, SeatRunError, run_seat  # noqa: E402


@pytest.fixture
def fake_provider(monkeypatch, tmp_path):
    def configure(source: str, prompt: str = "", cli: str = "ollama"):
        script = tmp_path / "provider.py"
        script.write_text(source, encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        env["PYTHONUTF8"] = "1"
        invocation = SeatInvocation(
            [sys.executable, "-u", str(script)], prompt, None, None, env, tmp_path,
        )
        monkeypatch.setattr(seat_process, "_build_seat_command", lambda *args: invocation)
        return {"cli": cli, "model": "offline-test"}
    return configure


def test_utf8_large_stdin_and_stderr_do_not_deadlock(fake_provider, tmp_path):
    prompt = "È una prova. 日本語. " * 20000
    seat = fake_provider(
        "import hashlib, sys\n"
        "sys.stderr.write('diagnostic ' * 20000)\n"
        "text = sys.stdin.read()\n"
        "print(hashlib.sha256(text.encode('utf-8')).hexdigest())\n"
        "print('VERDICT: APPROVE')\n",
        prompt,
    )
    import hashlib
    answer, _ = run_seat(seat, prompt, tmp_path, 10)
    assert answer == hashlib.sha256(prompt.encode("utf-8")).hexdigest() + "\nVERDICT: APPROVE\n"
    assert session._live_procs_snapshot() == []


def test_nonzero_exit_never_approves_partial_text(fake_provider, tmp_path):
    seat = fake_provider("import sys\nprint('VERDICT: APPROVE')\nsys.stderr.write('quota exhausted')\nsys.exit(7)\n")
    with pytest.raises(SeatRunError) as error:
        run_seat(seat, "", tmp_path, 10)
    assert error.value.kind == "process_error"
    assert "exit 7" in str(error.value)
    assert "quota exhausted" in str(error.value)


@pytest.mark.parametrize("cli", ["ollama", "opencode"])
def test_provider_error_redacts_secrets_before_exception_leaves_transport(fake_provider, tmp_path, cli):
    import json
    secret = "sk-" + "testcredential" * 3
    diagnostic = "quota exhausted\nAuthorization: " + secret
    source = (
        f"import sys\nsys.stderr.write({diagnostic!r})\nsys.exit(7)\n"
        if cli == "ollama" else
        f"print({json.dumps({'type': 'error', 'error': diagnostic})!r})\n"
    )
    seat = fake_provider(source, cli=cli)
    with pytest.raises(SeatRunError) as error:
        run_seat(seat, "", tmp_path, 10)
    assert secret not in str(error.value)
    assert "REDACTED" in str(error.value)
    assert error.value.kind == ("process_error" if cli == "ollama" else "seat_error")
    assert session._live_procs_snapshot() == []


def test_session_write_failure_preserves_previous_artifact(tmp_path, monkeypatch):
    import nexgen_core.files as files
    artifact = tmp_path / "consult.md"
    artifact.write_text("completed opinion\n", encoding="utf-8")

    def disk_full(_fd):
        raise OSError("synthetic full disk")

    monkeypatch.setattr(files.os, "fsync", disk_full)
    with pytest.raises(OSError, match="full disk"):
        session._write_private_text(artifact, "replacement\n")
    assert artifact.read_text(encoding="utf-8") == "completed opinion\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["consult.md"]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_timeout_detects_activity_without_a_newline(fake_provider, tmp_path, stream):
    seat = fake_provider(
        f"import sys, time\nsys.{stream}.write('È ancora in ragionamento')\n"
        f"sys.{stream}.flush()\ntime.sleep(30)\n"
    )
    with pytest.raises(SeatRunError) as error:
        run_seat(seat, "", tmp_path, 1)
    assert error.value.kind == "partial_timeout"
    assert "quota exhausted" not in str(error.value)
    assert session._live_procs_snapshot() == []


@pytest.mark.parametrize("noise", [
    "[]", "null", '{"type":"text","part":[]}',
    '{"type":"text","part":{"text":5}}',
    '{"type":"step_finish","part":{"tokens":"unknown"}}',
    '{"type":"step_finish","part":{"cost":[1]}}',
    '{"type":"step_finish","part":{"tokens":{"total":"unknown","input":5}}}',
])
def test_jsonl_noise_cannot_crash_a_valid_response(fake_provider, tmp_path, noise):
    import json
    response = {"type": "text", "part": {"text": "VERDICT: APPROVE"}}
    seat = fake_provider(f"print({noise!r})\nprint({json.dumps(response)!r})\n", cli="opencode")
    answer, _ = run_seat(seat, "", tmp_path, 10)
    assert answer == "VERDICT: APPROVE"


def test_jsonl_usage_sums_steps_without_counting_totals_twice(fake_provider, tmp_path):
    import json
    events = [
        {"type": "text", "part": {"text": "VERDICT: APPROVE"}},
        {"type": "step_finish", "part": {
            "tokens": {"total": 30, "input": 5, "output": 25},
            "cost": {"total": 0.1, "input": 0.05, "output": 0.05},
        }},
        {"type": "step_finish", "part": {"tokens": 10, "cost": 0.2}},
        {"type": "step_finish", "part": {
            "tokens": {"input": 2, "output": 3}, "cost": "unknown",
        }},
    ]
    source = "\n".join(f"print({json.dumps(event)!r})" for event in events)
    seat = fake_provider(source, cli="opencode")
    answer, usage = run_seat(seat, "", tmp_path, 10)
    assert answer == "VERDICT: APPROVE"
    assert usage["tokens"] == 45
    assert usage["cost"] == pytest.approx(0.3)


@pytest.mark.parametrize("shutdown", ["timeout", "cancel"])
def test_shutdown_closes_descendant_connections(fake_provider, tmp_path, shutdown):
    """A child inheriting the pipes must die together with its launcher."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(10)
        port = listener.getsockname()[1]
        child = (
            "import os, socket, time; "
            f"connection = socket.create_connection(('127.0.0.1', {port})); "
            "connection.sendall(str(os.getpid()).encode()); time.sleep(30)"
        )
        seat = fake_provider(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "print('provider started', flush=True)\ntime.sleep(30)\n"
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_seat, seat, "", tmp_path, 3 if shutdown == "timeout" else 30)
            connection, _ = listener.accept()
            with connection:
                child_pid = int(connection.recv(64))
                try:
                    if shutdown == "cancel":
                        assert session._cancel_all_procs() == 1
                    with pytest.raises(SeatRunError) as error:
                        future.result(timeout=15)
                    assert error.value.kind == ("partial_timeout" if shutdown == "timeout" else "process_error")
                    connection.settimeout(3)
                    try:
                        closed = connection.recv(1) == b""
                    except ConnectionResetError:
                        # Windows taskkill closes TCP with a reset; both
                        # reset and EOF prove the child lost its socket.
                        closed = True
                    assert closed
                    assert session._live_procs_snapshot() == []
                finally:
                    # Even a failed regression must not leave a test child.
                    try:
                        os.kill(child_pid, signal.SIGTERM)
                    except OSError:
                        pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal delivery; Windows tree cancellation tested separately")
@pytest.mark.parametrize("interruption", [signal.SIGTERM, signal.SIGINT])
def test_interrupted_consult_keeps_printed_opinions_and_stops_all_children(tmp_path, interruption):
    """Interrupt the real CLI with four live trees and one completed opinion."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(10)
        port = listener.getsockname()[1]
        provider = tmp_path / "provider.py"
        provider.write_text(
            "import os, socket, subprocess, sys, time\n"
            "if sys.argv[1] == 'fast':\n"
            "    print('Completed offline opinion.\\nVERDICT: APPROVE', flush=True)\n"
            "else:\n"
            "    child = 'import os, socket, time; c = socket.create_connection((\"127.0.0.1\", "
            + str(port) + ")); c.sendall(str(os.getpid()).encode()); time.sleep(30)'\n"
            "    subprocess.Popen([sys.executable, '-c', child])\n"
            "    time.sleep(30)\n", encoding="utf-8",
        )
        controller = tmp_path / "controller.py"
        controller.write_text(
            "import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(COUNCIL_DIR)!r})\n"
            "import council, seat_process\n"
            f"root = Path({str(tmp_path)!r})\n"
            "directory = root / 'session'\ndirectory.mkdir()\n"
            "names = ['slow1', 'slow2', 'slow3', 'slow4', 'fast']\n"
            "seats = {name: {'cli': 'ollama', 'model': name} for name in names}\n"
            "council.load_config = lambda: {'seats': seats}\n"
            "council.new_session_dir = lambda *args: directory\n"
            "seat_process._build_seat_command = lambda seat, *args: seat_process.SeatInvocation(\n"
            "    [sys.executable, '-u', str(root / 'provider.py'), seat['model']], None, None, None, None, root)\n"
            "args = ['consult', 'offline brief', '--rebuttals', '0']\n"
            "for name in names: args.extend(['--seat', name])\n"
            "sys.argv = ['council', *args]\ncouncil.main()\n", encoding="utf-8",
        )
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        env["PYTHONUTF8"] = "1"
        env["HOME"] = str(tmp_path)
        parent = subprocess.Popen([sys.executable, "-u", str(controller)], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        printed = threading.Event()
        output = []

        def read_output():
            for line in parent.stdout:
                output.append(line)
                if "Completed offline opinion." in line:
                    printed.set()

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        connections, child_pids = [], []
        try:
            for _ in range(4):
                connection, _ = listener.accept()
                connections.append(connection)
                child_pids.append(int(connection.recv(64)))
            assert printed.wait(5), "completed opinion must be visible before interrupting: " + "".join(output)
            os.kill(parent.pid, interruption)
            assert parent.wait(timeout=9) == -interruption
            for connection in connections:
                connection.settimeout(3)
                assert connection.recv(1) == b"", "a descendant survived Council shutdown"
            reader.join(timeout=3)
            assert "Completed offline opinion." in "".join(output)
            assert not (tmp_path / "session").exists()
        finally:
            if parent.poll() is None:
                parent.terminate()
            try:
                parent.wait(timeout=9)
            except subprocess.TimeoutExpired:
                parent.kill()
                parent.wait(timeout=3)
            for connection in connections:
                connection.close()
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
