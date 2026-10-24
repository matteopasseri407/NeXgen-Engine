"""The doctor can say where its own seconds go.

`doctor --summary` runs in the pre-push hook and in the verification of a
collection, so its duration is a promise to those callers. A promise nobody can
measure is a promise nobody can keep: before changing how the checks are
scheduled, the report has to name the check that spends the time.

Everything here runs on a fake home with an empty PATH: no check reaches the
owner's machine and no check can spawn a real command.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import doctor as doctor_mod  # noqa: E402

TIMING_LINE = re.compile(r"^(?P<seconds>\d+\.\d+)s\s+(?P<check>[a-z0-9_.-]+)$")


def _fake_home(tmp_path: Path, monkeypatch) -> Path:
    """A home that belongs to the test, with no command reachable on PATH."""
    home = tmp_path / "home"
    (home / "KnowledgeVault").mkdir(parents=True)
    (tmp_path / "empty-bin").mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(home / "KnowledgeVault"))
    monkeypatch.setenv("KNOWLEDGE_VAULT_REMOTE", "local")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    return home


def _timings(printed: str) -> list[tuple[str, float]]:
    found = []
    for line in printed.splitlines():
        match = TIMING_LINE.match(line.strip())
        if match:
            found.append((match.group("check"), float(match.group("seconds"))))
    return found


def test_the_summary_can_print_how_long_each_check_took(tmp_path, monkeypatch, capsys):
    _fake_home(tmp_path, monkeypatch)

    code = doctor_mod.main(["--summary", "--timings"])

    printed = capsys.readouterr().out
    assert "FAIL=" in printed, printed
    measured = _timings(printed)
    assert measured, f"nessuna durata stampata:\n{printed}"
    ids = {check for check, _ in measured}
    assert "env.state_dir" in ids
    assert code in (0, 1)


def test_the_timings_are_printed_slowest_first(tmp_path, monkeypatch, capsys):
    _fake_home(tmp_path, monkeypatch)

    doctor_mod.main(["--summary", "--timings"])

    measured = [seconds for _, seconds in _timings(capsys.readouterr().out)]
    assert measured == sorted(measured, reverse=True), measured


def test_the_timings_never_change_the_answer_or_appear_unasked(tmp_path, monkeypatch, capsys):
    _fake_home(tmp_path, monkeypatch)
    without = doctor_mod.main(["--summary"])
    plain = capsys.readouterr().out

    with_flag = doctor_mod.main(["--summary", "--timings"])
    timed = capsys.readouterr().out

    assert with_flag == without
    assert _timings(plain) == []
    assert not any("s  env.state_dir" in line for line in plain.splitlines())
    assert timed.splitlines()[0] == plain.splitlines()[0]


def test_the_command_the_hooks_run_can_print_the_timings_too(tmp_path, monkeypatch):
    """`nexgen doctor` is what the pre-push hook runs, and it is not this module's entry.

    It kept its own copy of the rendering, so a flag added only here would have
    been invisible on the path whose duration the hook is paying for.
    """
    import subprocess

    home = _fake_home(tmp_path, monkeypatch)
    entry = SCRIPTS_DIR / "nexgen_core" / "cli" / "__init__.py"

    def run(*args: str) -> subprocess.CompletedProcess:
        env = {
            "NEXGEN_HOME": str(home),
            "KNOWLEDGE_VAULT_PATH": str(home / "KnowledgeVault"),
            "KNOWLEDGE_VAULT_REMOTE": "local",
            "NEXGEN_DISABLE_HOST_MUTATIONS": "1",
            "PATH": str(tmp_path / "empty-bin"),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONIOENCODING": "utf-8",
        }
        return subprocess.run([sys.executable, str(entry), "doctor", *args], env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)

    timed = run("--summary", "--timings")
    plain = run("--summary")

    assert timed.stdout.splitlines()[0].startswith("FAIL="), timed.stdout + timed.stderr
    assert _timings(timed.stdout), timed.stdout + timed.stderr
    assert _timings(plain.stdout) == []
    assert timed.returncode == plain.returncode
