"""The node's own exec approvals, aligned only when the person asks for it.

`install.sh --role node --exec-policy full` (and `-ExecPolicy full` on Windows)
writes the body OpenClaw documents for a host that never stops on an approval.
Without the flag nothing is touched: those approvals belong to the PC, not to the
gateway's configuration. No test runs `openclaw`: the runner is a recorder.
"""
from __future__ import annotations

import json
from pathlib import Path

from nexgen_core import bootstrap, roles
from nexgen_core.openclaw import node_attach
from nexgen_core.openclaw.node_approvals import NEVER_PROMPT_BODY
from nexgen_core.openclaw_service import RunResult

ROOT = Path(__file__).resolve().parents[3]


class Recorder:
    """A runner that answers like a successful command and remembers what it was asked."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, cmd: list[str], stdin_text: str) -> RunResult:
        self.calls.append((list(cmd), stdin_text))
        return RunResult(self.returncode, "", "boom" if self.returncode else "")


def _finished(output: str) -> bool:
    """Whether the role reached its closing summary for this test's node."""
    return "node: test-node" in output


def role_inputs(tmp_path: Path) -> dict:
    """Everything `run_node_role` needs that is not the option under test."""
    return {
        "root": tmp_path,
        "home": tmp_path / "home",
        "target": "https://gateway.tailnet:18789/j/sample-join-code-123",
        "node_name": "test-node",
        "align_fn": lambda: (True, "aligned"),
        "runner": lambda cmd, timeout: RunResult(0, '{"connected": true}\n', ""),
        "wait_probe_fn": lambda: True,
    }


def test_the_documented_body_is_written_locally_on_the_node():
    recorder = Recorder()

    assert node_attach.apply_exec_policy(runner=recorder, out=lambda _: None) is True

    cmd, stdin_text = recorder.calls[0]
    assert cmd == ["openclaw", "approvals", "set", "--stdin"]
    # Local on purpose: no `--node`, it is this PC's own document.
    assert "--node" not in cmd
    assert json.loads(stdin_text) == {"version": 1, "defaults": {
        "security": "full", "ask": "off", "askFallback": "full",
    }}


def test_a_refused_write_changes_nothing_and_names_the_command():
    said: list[str] = []

    assert node_attach.apply_exec_policy(runner=Recorder(returncode=3), out=said.append) is False

    output = "\n".join(said)
    assert "left as they were" in output
    assert "openclaw approvals set --stdin" in output
    assert NEVER_PROMPT_BODY in output


def test_the_role_aligns_the_policy_after_attaching_and_says_so(tmp_path):
    recorder = Recorder()
    said: list[str] = []

    code = roles.run_node_role(**role_inputs(tmp_path), exec_policy="full", exec_runner=recorder, out=said.append)

    assert code == 0
    assert len(recorder.calls) == 1
    output = "\n".join(said)
    assert "no longer stops on an exec approval" in output
    assert _finished(output)


def test_without_the_option_nothing_on_the_node_is_changed(tmp_path):
    recorder = Recorder()
    said: list[str] = []

    code = roles.run_node_role(**role_inputs(tmp_path), exec_runner=recorder, out=said.append)

    assert code == 0
    assert recorder.calls == []
    assert "exec approval" not in "\n".join(said)
    assert _finished("\n".join(said))


def test_a_refused_alignment_fails_the_role_instead_of_pretending(tmp_path):
    said: list[str] = []

    code = roles.run_node_role(
        **role_inputs(tmp_path), exec_policy="full", exec_runner=Recorder(returncode=1), out=said.append,
    )

    assert code == 1
    output = "\n".join(said)
    assert "still stop on a prompt nobody answers" in output
    assert not _finished(output)


def test_an_unknown_policy_is_refused_before_anything_runs(tmp_path):
    recorder = Recorder()
    said: list[str] = []

    code = roles.run_node_role(**role_inputs(tmp_path), exec_policy="sometimes", exec_runner=recorder, out=said.append)

    assert code == 2
    assert recorder.calls == []
    assert "only 'full'" in "\n".join(said)


# ------------------------------------------------------------------ the two launchers


def test_the_windows_launcher_declares_the_option_and_forwards_it():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "[string]$ExecPolicy" in text
    assert "if ($ExecPolicy) { $forward += @('--exec-policy', $ExecPolicy) }" in text


def test_the_shell_launcher_forwards_every_argument_it_is_given():
    """`install.sh` has no flag list of its own: `"$@"` is what makes the option work."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'exec "$candidate" "$BOOTSTRAP" "$@"' in text
    assert "--exec-policy full" in text


def test_the_bootstrap_passes_the_option_to_the_role(tmp_path, monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(bootstrap, "preflight", lambda check=False: [])
    monkeypatch.setattr(bootstrap, "scaffold", lambda root, write=True: [])
    monkeypatch.setattr(bootstrap, "detect_clis", list)
    monkeypatch.setattr(bootstrap, "render", lambda *args, **kwargs: 0)
    monkeypatch.setattr(roles, "run_node_role", lambda *args, **kwargs: captured.update(kwargs) or 0)

    code = bootstrap._main_impl(["--root", str(tmp_path), "--role", "node", "--exec-policy", "full"])

    assert code == 0
    assert captured["exec_policy"] == "full"
