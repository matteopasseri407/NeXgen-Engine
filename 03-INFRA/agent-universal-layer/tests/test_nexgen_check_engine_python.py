"""`check_engine` runs the gate with the repo's own .venv when there is one.

The interpreter that runs the script may lack the dependencies (the Microsoft Store
Python has no PyYAML), and then the tests report false failures. These tests pin the
choice without starting any interpreter.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_engine.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_engine_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_venv_python(root: Path) -> Path:
    folder, name = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    path = root / ".venv" / folder / name
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    os.chmod(path, 0o755)
    return path


def test_the_repos_venv_is_preferred_to_the_running_interpreter(tmp_path):
    venv_python = _make_venv_python(tmp_path)
    assert _load()._resolve_python(tmp_path) == str(venv_python)


def test_without_a_venv_the_running_interpreter_is_used(tmp_path):
    assert _load()._resolve_python(tmp_path) == sys.executable
