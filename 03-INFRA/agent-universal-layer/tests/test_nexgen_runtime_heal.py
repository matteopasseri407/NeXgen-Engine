"""The engine's environment repairs itself when the code it runs needs a library it lacks.

An update merges the new tree and runs that tree's commands before it provisions the
environment, and the updater doing it is the previous release's. A release that adds a
dependency (3.0 adds filelock, psutil, tomlkit and the MCP SDK) used to fail the first
of those commands, publishing the engine pin, and roll itself back on every machine.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from nexgen_core import bootstrap_runtime, runtime
from nexgen_core.bootstrap_runtime import HEAL_MARKER, heal_running_environment
from nexgen_core.runtime import RuntimeProvisionError

ENGINE_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def own_runtime(tmp_path, monkeypatch) -> Path:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("NEXGEN_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.delenv(HEAL_MARKER, raising=False)
    return runtime_dir


class Recorder:
    def __init__(self, rc: int = 0, fail: bool = False):
        self.rc, self.fail = rc, fail
        self.ensured: list[tuple[Path, Path]] = []
        self.calls: list[tuple[list[str], dict]] = []

    def ensure(self, runtime_dir, clone, **_):
        if self.fail:
            raise RuntimeProvisionError("pip could not install the engine's dependencies")
        self.ensured.append((runtime_dir, clone))

    def call(self, command, env):
        self.calls.append((command, env))
        return self.rc


def _heal(argv, runtime_dir, recorder, *, prefix=None, missing=("filelock",), environ=None):
    return heal_running_environment(
        argv, entry="cli.py", environ=environ if environ is not None else {},
        prefix=str(prefix if prefix is not None else runtime_dir),
        missing=lambda: list(missing), ensure=recorder.ensure, call=recorder.call,
    )


def test_a_missing_library_in_the_engine_environment_is_installed_and_the_command_rerun(own_runtime):
    recorder = Recorder(rc=0)

    assert _heal(["vault", "push", "99-INDEX/ENGINE-PIN.txt"], own_runtime, recorder) == 0

    assert recorder.ensured == [(own_runtime, bootstrap_runtime._engine_clone())]
    command, env = recorder.calls[0]
    assert command[1:] == ["cli.py", "vault", "push", "99-INDEX/ENGINE-PIN.txt"]
    assert env[HEAL_MARKER] == "1"


def test_the_rerun_command_answers_for_the_repair(own_runtime):
    assert _heal(["vault", "push"], own_runtime, Recorder(rc=3)) == 3


def test_another_interpreter_is_left_alone(own_runtime, tmp_path):
    recorder = Recorder()

    assert _heal(["vault", "push"], own_runtime, recorder, prefix=tmp_path / "some-venv") is None
    assert recorder.ensured == [] and recorder.calls == []


def test_an_environment_that_imports_everything_runs_the_command_itself(own_runtime):
    recorder = Recorder()

    assert _heal(["doctor"], own_runtime, recorder, missing=()) is None
    assert recorder.ensured == []


def test_a_command_already_rerun_is_never_repaired_again(own_runtime):
    recorder = Recorder()

    assert _heal(["vault", "push"], own_runtime, recorder, environ={HEAL_MARKER: "1"}) is None
    assert recorder.ensured == []


def test_the_runtime_command_itself_is_not_intercepted(own_runtime):
    recorder = Recorder()

    assert _heal(["runtime", "ensure"], own_runtime, recorder) is None
    assert recorder.ensured == []


def test_a_repair_that_fails_is_a_failure_with_its_reason(own_runtime, capsys):
    recorder = Recorder(fail=True)

    assert _heal(["vault", "push"], own_runtime, recorder) == 1
    assert "could not install" in capsys.readouterr().err
    assert recorder.calls == []


def _distribution(requirement: str) -> str:
    return re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0].strip()


def test_every_declared_dependency_is_checked_as_essential():
    """A dependency the check does not list is a library whose absence nobody repairs."""
    declared = tomllib.loads((ENGINE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]

    assert {_distribution(item).lower() for item in declared} == {
        name.lower() for name in runtime.ESSENTIAL_IMPORTS.values()
    }
