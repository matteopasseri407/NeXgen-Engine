"""The AGENTS.md bootstrap judged against the read limit of each installed harness.

Every size here is a literal: 24,000 bytes is Antigravity's documented per-file
limit and 32,768 Codex's default `project_doc_max_bytes`. Nothing is recomputed
from the code under test, so a change that invents a different number fails a
test instead of agreeing with itself.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import doctor  # noqa: E402 - the engine has to be importable before its tests run
from nexgen_core.checks import instruction_limits_checks  # noqa: E402
from nexgen_core.paths import canonical_instructions  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")

CHECK = instruction_limits_checks.check_bootstrap_reader_limits


def _installed(tmp_path: Path, monkeypatch, *names: str) -> None:
    """A PATH holding exactly the harnesses named: 'installed' is a fact of the test."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        if sys.platform == "win32":
            stub = bin_dir / f"{name}.cmd"
            stub.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
        else:
            stub = bin_dir / name
            stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir))


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _write_canon(vault: Path, size: int = 100) -> Path:
    canon = canonical_instructions(vault)
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_bytes(b"x" * size)
    return canon


def _write_reader_file(home: Path, relative: tuple[str, ...], size: int) -> Path:
    path = home.joinpath(*relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


_ANTIGRAVITY = (".gemini", "config", "AGENTS.md")
_CODEX = (".codex", "AGENTS.md")


def test_past_the_documented_limit_is_broken(tmp_path: Path, monkeypatch):
    """Antigravity truncates at 24,000 bytes: one byte more is rules lost silently."""
    vault, home = tmp_path / "vault", _home(tmp_path)
    _write_canon(vault)
    _installed(tmp_path, monkeypatch, "agy")
    _write_reader_file(home, _ANTIGRAVITY, 24001)

    outcome = CHECK(vault, home)

    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "Antigravity" in outcome.message
    assert outcome.detail is not None and "24000" in outcome.detail


def test_at_ninety_percent_of_the_limit_warns_before_anything_is_dropped(tmp_path: Path, monkeypatch):
    """21,600 is exactly 90% of 24,000: the doctor speaks while there is still room."""
    vault, home = tmp_path / "vault", _home(tmp_path)
    _write_canon(vault)
    _installed(tmp_path, monkeypatch, "agy")
    _write_reader_file(home, _ANTIGRAVITY, 21600)

    outcome = CHECK(vault, home)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "90" in outcome.message


def test_one_byte_below_ninety_percent_is_ok(tmp_path: Path, monkeypatch):
    vault, home = tmp_path / "vault", _home(tmp_path)
    _write_canon(vault)
    _installed(tmp_path, monkeypatch, "agy")
    _write_reader_file(home, _ANTIGRAVITY, 21599)

    outcome = CHECK(vault, home)

    assert outcome is not None
    assert outcome.severity == Severity.OK


def test_each_harness_is_compared_with_its_own_limit(tmp_path: Path, monkeypatch):
    """24,001 bytes is fine for Codex (32,768) and fatal for Antigravity: only the
    installed harness that would truncate is reported."""
    vault, home = tmp_path / "vault", _home(tmp_path)
    _write_canon(vault)
    _installed(tmp_path, monkeypatch, "codex")
    codex_file = _write_reader_file(home, _CODEX, 24001)

    assert CHECK(vault, home).severity == Severity.OK

    codex_file.write_bytes(b"x" * 32769)
    assert CHECK(vault, home).severity == Severity.BROKEN


def test_a_copy_of_the_bootstrap_is_measured_as_the_runtime_reads_it(tmp_path: Path, monkeypatch):
    """Windows without symlink privileges gets an identical copy (guard fallback):
    it must be measured too, and at its real size."""
    vault, home = tmp_path / "vault", _home(tmp_path)
    canon = _write_canon(vault, size=100)
    _installed(tmp_path, monkeypatch, "codex")
    copy = home / ".codex" / "AGENTS.md"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"x" * 32769)
    assert not copy.is_symlink()
    assert canon.stat().st_size == 100

    assert CHECK(vault, home).severity == Severity.BROKEN


def test_no_installed_harness_is_not_reported(tmp_path: Path, monkeypatch):
    """A machine with no harness reading a bootstrap has no limit to exceed."""
    vault, home = tmp_path / "vault", _home(tmp_path)
    _write_canon(vault)
    _installed(tmp_path, monkeypatch)  # empty PATH
    _write_reader_file(home, _ANTIGRAVITY, 24001)

    assert CHECK(vault, home) is None


def test_an_installed_harness_without_its_file_is_not_reported(tmp_path: Path, monkeypatch):
    """Installed but never synced: it reads nothing, so nothing is truncated."""
    vault, home = tmp_path / "vault", _home(tmp_path)
    _write_canon(vault)
    _installed(tmp_path, monkeypatch, "codex")

    assert CHECK(vault, home) is None


def test_a_harness_with_no_documented_limit_is_never_enforced(tmp_path: Path, monkeypatch):
    """OpenCode documents no byte limit for its AGENTS.md: a 100,000-byte file
    must stay silent instead of inventing a fault from a number nobody wrote."""
    vault, home = tmp_path / "vault", _home(tmp_path)
    _write_canon(vault)
    _installed(tmp_path, monkeypatch, "opencode")
    _write_reader_file(home, (".config", "opencode", "AGENTS.md"), 100_000)

    assert CHECK(vault, home) is None


def test_a_missing_bootstrap_is_left_to_the_presence_check(tmp_path: Path, monkeypatch):
    vault, home = tmp_path / "vault", _home(tmp_path)
    vault.mkdir(exist_ok=True)
    _installed(tmp_path, monkeypatch, "agy")
    _write_reader_file(home, _ANTIGRAVITY, 24001)

    assert CHECK(vault, home) is None


def test_the_doctor_registers_the_check(tmp_path: Path):
    """A check nobody runs is a comment: the wiring itself is asserted."""
    registered: list[str] = []
    doctor._run_instruction_checks(
        lambda check_id, produce: registered.append(check_id), tmp_path, tmp_path,
    )

    assert "instructions.reader_limits" in registered


def test_the_claude_pointer_is_measured_through_to_the_bootstrap_it_names(tmp_path: Path, monkeypatch):
    """Claude reads the pointer and then the file it names: measure the file.

    Measuring the ~300-byte pointer made this row OK by construction, so the one case it exists
    for -- a bootstrap Claude skips whole because it is over 4 MiB -- was never measured.
    """
    vault, home = tmp_path / "vault", _home(tmp_path)
    canon = _write_canon(vault, size=4194305)  # 4 MiB = 4194304: one byte more is skipped
    _installed(tmp_path, monkeypatch, "claude")
    pointer = home / ".claude" / "CLAUDE.md"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(f"# Claude compatibility pointer\n\nCanonical instructions live at:\n{canon}\n", encoding="utf-8")
    assert pointer.stat().st_size < 1000

    outcome = CHECK(vault, home)

    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "Claude Code (4194305/4194304 bytes)" in outcome.message
