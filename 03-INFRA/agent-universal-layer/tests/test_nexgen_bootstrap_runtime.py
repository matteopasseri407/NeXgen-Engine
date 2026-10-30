"""The bootstrap builds the engine's own Python environment before it reads YAML.

A clean Ubuntu has python3 without PyYAML and a pip that refuses to install one
(PEP 668), so the old "pip install pyyaml" remedy did not exist. The engine now
provisions from `pyproject.toml` and runs the same bootstrap inside it, once.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import bootstrap, bootstrap_runtime, i18n  # noqa: E402
from nexgen_core.runtime import RuntimeProvisionError, runtime_python  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch):
    """English messages, and no re-exec marker escaping into another test."""
    monkeypatch.delenv(bootstrap_runtime.REEXEC_MARKER, raising=False)
    i18n.set_language("en")
    yield
    os.environ.pop(bootstrap_runtime.REEXEC_MARKER, None)
    i18n.set_language(None)


def _fake_provision(monkeypatch, tmp_path: Path) -> dict[str, list]:
    """Records provisioning and the re-exec instead of running either."""
    calls: dict[str, list] = {"ensure": [], "exec": []}

    def ensure(runtime_dir, engine_clone):
        calls["ensure"].append((runtime_dir, engine_clone))

    def execv(path, args):
        calls["exec"].append((path, args))

    monkeypatch.setattr(bootstrap_runtime, "ensure_runtime", ensure)
    monkeypatch.setattr(bootstrap_runtime.os, "execv", execv)
    monkeypatch.setattr(bootstrap_runtime, "resolve_runtime_dir", lambda home=None: tmp_path / "runtime")
    return calls


def _quiet_install(monkeypatch) -> None:
    """Everything after the prerequisites is not the subject of these tests."""
    monkeypatch.setattr(bootstrap, "scaffold", lambda root, write: [])
    monkeypatch.setattr(bootstrap, "detect_clis", lambda *a, **k: [])
    monkeypatch.setattr(bootstrap, "desktop_app_orphans", lambda *a, **k: [])


def test_missing_yaml_provisions_and_reruns_the_same_bootstrap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap_runtime, "yaml_available", lambda: False)
    calls = _fake_provision(monkeypatch, tmp_path)

    stopped = bootstrap_runtime.provision_before_prerequisites(["--role", "server"])

    assert stopped is None
    assert capsys.readouterr().out.count("\n") == 1, "one line, before the slow part"
    runtime_dir, engine_clone = calls["ensure"][0]
    assert runtime_dir == tmp_path / "runtime"
    assert engine_clone == bootstrap.SCRIPTS_DIR.parents[1]
    assert (engine_clone / "pyproject.toml").is_file()
    path, args = calls["exec"][0]
    assert Path(path) == runtime_python(tmp_path / "runtime")
    assert args == [str(runtime_python(tmp_path / "runtime")),
                    str(Path(bootstrap.__file__).resolve()), "--role", "server"]
    assert os.environ[bootstrap_runtime.REEXEC_MARKER] == "1"


def test_the_marker_never_provisions_twice_and_the_install_stops(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap_runtime, "yaml_available", lambda: False)
    monkeypatch.setenv(bootstrap_runtime.REEXEC_MARKER, "1")
    calls = _fake_provision(monkeypatch, tmp_path)
    _quiet_install(monkeypatch)

    code = bootstrap.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 1
    assert "PyYAML" in out and "Something required is missing" in out
    assert calls["ensure"] == [] and calls["exec"] == []


def test_a_check_run_creates_nothing_and_does_not_fail_over_yaml(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap_runtime, "yaml_available", lambda: False)
    calls = _fake_provision(monkeypatch, tmp_path)
    _quiet_install(monkeypatch)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    code = bootstrap.main(["--check", "--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before
    assert calls["ensure"] == [] and calls["exec"] == []
    assert "PyYAML" in out
    assert "the installer creates it in the engine's own Python environment" in out


def test_a_provisioning_error_is_printed_and_stops_the_install(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap_runtime, "yaml_available", lambda: False)
    calls = _fake_provision(monkeypatch, tmp_path)
    attempted: list[Path] = []

    def boom(runtime_dir, engine_clone):
        attempted.append(runtime_dir)
        raise RuntimeProvisionError(
            "could not create the engine environment (on Debian this usually means python3-venv is missing)"
        )

    monkeypatch.setattr(bootstrap_runtime, "ensure_runtime", boom)
    _quiet_install(monkeypatch)

    code = bootstrap.main(["--root", str(tmp_path)])
    captured = capsys.readouterr()

    assert code == 1
    assert attempted == [tmp_path / "runtime"]
    assert "python3-venv" in captured.err
    assert calls["exec"] == []


def test_yaml_present_provisions_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap_runtime, "yaml_available", lambda: True)
    calls = _fake_provision(monkeypatch, tmp_path)
    _quiet_install(monkeypatch)

    assert bootstrap_runtime.provision_before_prerequisites(["--root", str(tmp_path)]) is None
    assert bootstrap.main(["--check", "--root", str(tmp_path)]) == 0
    assert calls["ensure"] == [] and calls["exec"] == []


def test_the_pyyaml_remedy_no_longer_points_at_pip():
    finding = next(f for f in bootstrap.preflight() if f.label == "PyYAML")

    assert "pip install" not in finding.remedy
    assert "python3-venv" in finding.remedy


def test_a_check_run_borrows_an_environment_an_earlier_install_built(tmp_path, monkeypatch):
    """Installed once, `install.sh` still starts on the system python: `--check` reads in the engine's."""
    monkeypatch.setattr(bootstrap_runtime, "yaml_available", lambda: False)
    calls = _fake_provision(monkeypatch, tmp_path)
    python = runtime_python(tmp_path / "runtime")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")

    assert bootstrap_runtime.provision_before_prerequisites(["--role", "server", "--check"], check=True) is None
    assert calls["ensure"] == []
    assert calls["exec"][0][1][2:] == ["--role", "server", "--check"]


def test_a_role_check_without_the_environment_says_why_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    """Seen on a clean Ubuntu 24.04: `--role server --check` died on `import yaml` inside the role code."""
    monkeypatch.setattr(bootstrap_runtime, "yaml_available", lambda: False)
    calls = _fake_provision(monkeypatch, tmp_path)
    _quiet_install(monkeypatch)

    code = bootstrap.main(["--role", "server", "--check", "--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "run it once without --check" in out
    assert calls["ensure"] == [] and calls["exec"] == []
