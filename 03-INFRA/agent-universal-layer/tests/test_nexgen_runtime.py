"""The engine's own Python environment, the launchers that use it, and the wheel that carries the layer.

On the machine this was found on, the Council's resumable relay answered "missing
dependency" and the `drive` MCP server came up with no tools: the launchers started
whatever `python3` was first on PATH, which had PyYAML and none of the libraries the
engine declares as core, and the doctor only asked about PyYAML.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REAL_VAULT
from nexgen_core import paths, runtime, shims
from nexgen_core.checks.runtime_checks import check_engine_runtime
from nexgen_core.report import Severity

needs_setuptools = pytest.mark.skipif(importlib.util.find_spec("setuptools") is None, reason="setuptools is needed to build")


# ---------------------------------------------------------------- the dependency list


def test_the_dependency_list_is_the_one_in_pyproject():
    deps = runtime.declared_dependencies(REAL_VAULT)
    assert any(d.lower().startswith("langgraph") for d in deps)
    assert any(d.lower().startswith("mcp") for d in deps)


def test_an_unreadable_dependency_list_is_a_provisioning_error_not_a_crash(tmp_path):
    with pytest.raises(runtime.RuntimeProvisionError, match="dependency list"):
        runtime.declared_dependencies(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    with pytest.raises(runtime.RuntimeProvisionError):
        runtime.declared_dependencies(tmp_path)


def test_every_essential_module_is_covered_by_a_declared_dependency():
    """The check and the list must not drift: a module checked here that pyproject
    does not install would make the environment permanently unverifiable."""
    declared = " ".join(runtime.declared_dependencies(REAL_VAULT)).lower().replace("_", "-")
    for module, distribution in runtime.ESSENTIAL_IMPORTS.items():
        assert distribution.lower().replace("_", "-") in declared, (module, distribution)


def test_fingerprint_changes_with_the_dependencies():
    assert runtime.fingerprint(["a>=1"]) == runtime.fingerprint(["a>=1"])
    assert runtime.fingerprint(["a>=1"]) != runtime.fingerprint(["a>=2"])
    assert runtime.fingerprint(["a", "b"]) == runtime.fingerprint(["b", "a"])


def test_a_missing_parent_package_counts_as_missing_not_as_a_crash(monkeypatch):
    real = runtime.importlib.util.find_spec

    def find_spec(name, *a, **k):
        if name == "langgraph.checkpoint.sqlite":
            raise ModuleNotFoundError("No module named 'langgraph'")
        return real(name, *a, **k)

    monkeypatch.setattr(runtime.importlib.util, "find_spec", find_spec)
    assert runtime.missing_imports(["langgraph.checkpoint.sqlite", "os"]) == ["langgraph.checkpoint.sqlite"]


# ---------------------------------------------------------------- provisioning


class FakeRun:
    """Stands in for subprocess.run: creates the venv's python, 'installs', reports what is missing."""

    def __init__(self, *, pip_rc: int = 0, venv_rc: int = 0, still_missing: str = ""):
        self.calls: list[list[str]] = []
        self.pip_rc, self.venv_rc, self.still_missing = pip_rc, venv_rc, still_missing

    def __call__(self, args, **_kwargs):
        args = [str(a) for a in args]
        self.calls.append(args)
        if "venv" in args:
            if self.venv_rc == 0:
                target = Path(args[-1])
                py = runtime.runtime_python(target)
                py.parent.mkdir(parents=True, exist_ok=True)
                py.write_text("#!/bin/sh\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, self.venv_rc, "", "ensurepip is not available" if self.venv_rc else "")
        if "pip" in args:
            return subprocess.CompletedProcess(args, self.pip_rc, "", "network unreachable" if self.pip_rc else "")
        return subprocess.CompletedProcess(args, 0, self.still_missing, "")  # the verify step


def _checkout(tmp_path: Path, deps: str = '"PyYAML>=6.0"') -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text(f"[project]\nname='x'\ndependencies=[{deps}]\n", encoding="utf-8")
    return checkout


def test_provisioning_creates_installs_verifies_and_only_then_stamps(tmp_path):
    run, rt = FakeRun(), tmp_path / "runtime"

    result = runtime.ensure_runtime(rt, _checkout(tmp_path), run=run, log=lambda _l: None)

    assert result.changed is True
    kinds = ["venv" if "venv" in c else "pip" if "pip" in c else "verify" for c in run.calls]
    assert kinds == ["venv", "pip", "verify"]
    assert (rt / runtime.STAMP_NAME).is_file()
    assert "PyYAML>=6.0" in run.calls[1]


def test_a_current_environment_is_left_alone(tmp_path):
    checkout, rt = _checkout(tmp_path), tmp_path / "runtime"
    runtime.ensure_runtime(rt, checkout, run=FakeRun(), log=lambda _l: None)
    again = FakeRun()

    result = runtime.ensure_runtime(rt, checkout, run=again, log=lambda _l: None)

    assert result.changed is False
    assert [c for c in again.calls if "pip" in c or "venv" in c] == []  # no reinstall, no rebuild


def test_changed_dependencies_reinstall_without_recreating_the_environment(tmp_path):
    rt = tmp_path / "runtime"
    runtime.ensure_runtime(rt, _checkout(tmp_path), run=FakeRun(), log=lambda _l: None)
    (tmp_path / "checkout" / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['PyYAML>=6.0','httpx>=0.28']\n", encoding="utf-8")
    run = FakeRun()

    result = runtime.ensure_runtime(rt, tmp_path / "checkout", run=run, log=lambda _l: None)

    assert result.changed is True
    assert not any("venv" in c for c in run.calls) and any("pip" in c for c in run.calls)


def test_a_failed_install_leaves_no_stamp_so_the_launchers_do_not_trust_it(tmp_path):
    rt = tmp_path / "runtime"
    with pytest.raises(runtime.RuntimeProvisionError, match="network"):
        runtime.ensure_runtime(rt, _checkout(tmp_path), run=FakeRun(pip_rc=1), log=lambda _l: None)
    assert not (rt / runtime.STAMP_NAME).exists()


def test_an_environment_that_still_cannot_import_is_not_stamped(tmp_path):
    rt = tmp_path / "runtime"
    with pytest.raises(runtime.RuntimeProvisionError, match="mcp"):
        runtime.ensure_runtime(rt, _checkout(tmp_path), run=FakeRun(still_missing="mcp"), log=lambda _l: None)
    assert not (rt / runtime.STAMP_NAME).exists()


def test_a_stale_stamp_does_not_survive_a_rebuild_that_fails(tmp_path):
    checkout, rt = _checkout(tmp_path), tmp_path / "runtime"
    runtime.ensure_runtime(rt, checkout, run=FakeRun(), log=lambda _l: None)
    (checkout / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=['other']\n", encoding="utf-8")
    with pytest.raises(runtime.RuntimeProvisionError):
        runtime.ensure_runtime(rt, checkout, run=FakeRun(pip_rc=1), log=lambda _l: None)
    assert not (rt / runtime.STAMP_NAME).exists()  # the old stamp would vouch for a half-rebuilt environment


def test_a_system_without_venv_support_says_what_to_install(tmp_path):
    with pytest.raises(runtime.RuntimeProvisionError, match="python3-venv"):
        runtime.ensure_runtime(tmp_path / "runtime", _checkout(tmp_path), run=FakeRun(venv_rc=1), log=lambda _l: None)


# ---------------------------------------------------------------- the launchers


@pytest.mark.skipif(os.name == "nt", reason="exercises the POSIX launcher with sh")
class TestPosixLauncher:
    """Runs the generated launcher itself: what it chooses is what a person gets."""

    @staticmethod
    def _launcher(tmp_path: Path, runtime_dir: Path) -> Path:
        entry = tmp_path / "entry.py"
        entry.write_text("import sys; print('SYSTEM-PYTHON', *sys.argv[1:])\n", encoding="utf-8")
        launcher = tmp_path / "nexgen"
        launcher.write_text(shims._render("nexgen", [], entry, False, runtime_dir), encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
        return launcher

    @staticmethod
    def _run(launcher: Path, *args: str) -> str:
        env = {"PATH": os.environ["PATH"], "HOME": str(launcher.parent)}  # no AGENT_ENGINE_ROOT from the host
        return subprocess.run([str(launcher), *args], capture_output=True, text=True, env=env, check=True).stdout.strip()

    def _fake_runtime(self, runtime_dir: Path, *, stamped: bool) -> None:
        py = runtime.runtime_python(runtime_dir)
        py.parent.mkdir(parents=True)
        py.write_text('#!/bin/sh\necho "RUNTIME-PYTHON $@"\n', encoding="utf-8")
        py.chmod(py.stat().st_mode | stat.S_IEXEC)
        if stamped:
            (runtime_dir / runtime.STAMP_NAME).write_text("{}", encoding="utf-8")

    def test_uses_the_provisioned_environment(self, tmp_path):
        rt = tmp_path / "runtime"
        self._fake_runtime(rt, stamped=True)
        assert self._run(self._launcher(tmp_path, rt), "doctor").startswith("RUNTIME-PYTHON")

    def test_falls_back_to_a_system_python_until_it_is_provisioned(self, tmp_path):
        rt = tmp_path / "runtime"
        self._fake_runtime(rt, stamped=False)  # the interpreter is there, the verified stamp is not
        assert self._run(self._launcher(tmp_path, rt), "doctor") == "SYSTEM-PYTHON doctor"
        assert self._run(self._launcher(tmp_path, tmp_path / "never-created"), "x") == "SYSTEM-PYTHON x"

    def test_a_dangling_environment_falls_back_instead_of_failing(self, tmp_path):
        rt = tmp_path / "runtime"
        self._fake_runtime(rt, stamped=True)
        py = runtime.runtime_python(rt)
        py.unlink()
        py.symlink_to(tmp_path / "gone")  # the system python it pointed at was upgraded away
        assert self._run(self._launcher(tmp_path, rt), "doctor") == "SYSTEM-PYTHON doctor"

    def test_arguments_survive_in_both_branches(self, tmp_path):
        rt = tmp_path / "runtime"
        self._fake_runtime(rt, stamped=True)
        assert self._run(self._launcher(tmp_path, rt), "a b", "c") == "RUNTIME-PYTHON " + str(self._entry(tmp_path)) + " a b c"

    @staticmethod
    def _entry(tmp_path: Path) -> Path:
        return tmp_path / "entry.py"


def test_the_windows_launcher_has_the_same_rule():
    text = shims._render("nexgen", [], Path("C:/e/__init__.py"), True, Path("C:/rt"))
    assert 'if exist "%NEXGEN_RUNTIME%\\.provisioned" if exist "%NEXGEN_RUNTIME%\\Scripts\\python.exe"' in text
    assert text.index("NEXGEN_RUNTIME") < text.index("where py")  # tried before any system python


def test_install_shims_reports_only_what_it_rewrote(tmp_path):
    bin_dir = tmp_path / "bin"
    first: list[str] = []
    everything = shims.install_shims(bin_dir=bin_dir, home=tmp_path, changed=first)
    assert len(first) == len(everything) and len(everything) > 5

    second: list[str] = []
    shims.install_shims(bin_dir=bin_dir, home=tmp_path, changed=second)
    assert second == []  # nothing to hash, nothing to read: the guard asked this by reading every file

    victim = next(p for p in bin_dir.iterdir() if p.name == "nexgen")
    victim.write_text("tampered\n", encoding="utf-8")
    third: list[str] = []
    shims.install_shims(bin_dir=bin_dir, home=tmp_path, changed=third)
    assert third == [str(victim)]


def test_a_package_install_leaves_the_package_managers_launchers_alone(tmp_path, monkeypatch):
    """pipx and uv put their own `nexgen` in ~/.local/bin; the guard used to replace it with a
    launcher that starts whatever python3 is on PATH, which has none of the tool's libraries."""
    monkeypatch.setattr(shims, "installed_as_package", lambda: True)
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "nexgen").write_text("pipx launcher\n", encoding="utf-8")
    changed: list[str] = []

    assert shims.install_shims(home=tmp_path, changed=changed) == []

    assert (bin_dir / "nexgen").read_text(encoding="utf-8") == "pipx launcher\n" and changed == []
    # An explicit bin_dir (tests, tools) is still honoured.
    assert shims.install_shims(bin_dir=tmp_path / "explicit", home=tmp_path)


# ---------------------------------------------------------------- where things live


def test_the_runtime_lives_outside_the_checkout_and_a_sandbox_keeps_its_own(tmp_path, monkeypatch):
    home = tmp_path / "home"
    assert paths.resolve_runtime_dir(home) == home / ".local" / "share" / "nexgen-engine" / "runtime"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.resolve_runtime_dir(home) == home / ".local" / "share" / "nexgen-engine" / "runtime"  # sandbox home
    assert paths.resolve_runtime_dir(Path.home()) == tmp_path / "xdg" / "nexgen-engine" / "runtime"
    monkeypatch.setenv("NEXGEN_RUNTIME_DIR", str(tmp_path / "explicit"))
    assert paths.resolve_runtime_dir(home) == tmp_path / "explicit"
    monkeypatch.setenv("NEXGEN_RUNTIME_DIR", "relative/path")
    assert paths.resolve_runtime_dir(home) != Path("relative/path")  # a relative value anchors at the CWD: ignored


def test_without_a_checkout_the_engine_root_is_the_layer_bundled_in_the_wheel(tmp_path, monkeypatch):
    bundled = tmp_path / "site-packages" / "nexgen_core" / "_engine"
    (bundled / "agent-universal-layer").mkdir(parents=True)
    monkeypatch.setattr(paths, "bundled_engine_root", lambda: bundled)

    assert paths.resolve_engine_root(tmp_path / "home") == bundled
    # A checkout, when there is one, still wins: that is where a developer's edits live.
    checkout = tmp_path / "home" / ".nexgen-engine" / "03-INFRA"
    checkout.mkdir(parents=True)
    assert paths.resolve_engine_root(tmp_path / "home") == checkout


def test_a_package_install_is_recognised_by_where_the_code_lives(monkeypatch):
    monkeypatch.setattr(paths, "__file__", "/home/user/.local/share/uv/tools/nexgen/lib/python3.12/site-packages/nexgen_core/paths.py")
    assert paths.installed_as_package() is True
    monkeypatch.setattr(paths, "__file__", "/home/user/NeXgen-Engine/03-INFRA/scripts/nexgen_core/paths.py")
    assert paths.installed_as_package() is False  # a checkout, an editable install too


# ---------------------------------------------------------------- the doctor, init and the guard phase


def test_the_doctor_names_what_is_missing_and_the_command_that_fixes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "missing_imports", lambda *_a: ["langgraph", "mcp"])
    monkeypatch.setattr("nexgen_core.checks.runtime_checks.installed_as_package", lambda: False)
    engine_root = tmp_path / "clone" / "03-INFRA"
    engine_root.mkdir(parents=True)

    outcome = check_engine_runtime(tmp_path / "home", engine_root)

    assert outcome.severity == Severity.BROKEN
    assert "langgraph" in outcome.message and "mcp" in outcome.message
    assert "nexgen runtime ensure" in outcome.action
    assert outcome.remedy is None  # no pyproject beside it, so nothing to provision from
    (tmp_path / "clone" / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=[]\n", encoding="utf-8")
    assert check_engine_runtime(tmp_path / "home", engine_root).remedy is not None


def test_the_doctor_for_a_package_install_says_to_reinstall(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "missing_imports", lambda *_a: ["mcp"])
    monkeypatch.setattr("nexgen_core.checks.runtime_checks.installed_as_package", lambda: True)
    outcome = check_engine_runtime(tmp_path, tmp_path / "x")
    assert outcome.severity == Severity.BROKEN and "package manager" in outcome.action and outcome.remedy is None


def test_the_doctor_is_green_when_everything_imports(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "missing_imports", lambda *_a: [])
    assert check_engine_runtime(tmp_path, tmp_path).severity == Severity.OK


def test_init_check_no_longer_runs_the_real_installation(monkeypatch):
    from nexgen_core import bootstrap
    from nexgen_core.cli import engine

    seen: list[list[str]] = []
    monkeypatch.setattr(bootstrap, "main", lambda argv: seen.append(list(argv)) or 0)
    args = type("A", (), {"check": True, "local": False, "root": None})()

    assert engine.cmd_init(args) == 0
    assert seen == [["--check"]]


def test_init_in_a_package_install_refuses_to_scaffold_inside_the_environment(monkeypatch, capsys):
    from nexgen_core import bootstrap

    monkeypatch.setattr(bootstrap, "installed_as_package", lambda: True)
    monkeypatch.setattr(bootstrap, "render", lambda *_a, **_k: 0)

    assert bootstrap.main(["--check"]) == 2
    assert "--root" in capsys.readouterr().err


def test_the_guard_runtime_phase_provisions_only_when_allowed_and_possible(tmp_path, monkeypatch):
    from nexgen_core.guard import GuardRunner

    checkout = tmp_path / "clone"
    (checkout / "03-INFRA").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=[]\n", encoding="utf-8")
    runner = GuardRunner(vault_data=tmp_path / "vault", engine_root=checkout / "03-INFRA", home=tmp_path / "home")
    calls: list[tuple] = []
    monkeypatch.setattr(runtime, "ensure_runtime", lambda *a, **k: calls.append(a) or runtime.RuntimeResult(True, "ok"))
    monkeypatch.setattr("nexgen_core.paths.installed_as_package", lambda: False)

    actions: list[str] = []
    monkeypatch.setenv("NEXGEN_DISABLE_HOST_MUTATIONS", "1")  # the suite's default: it must not download anything
    runner._phase_runtime(actions)
    assert calls == [] and actions

    monkeypatch.delenv("NEXGEN_DISABLE_HOST_MUTATIONS")
    actions.clear()
    runner._phase_runtime(actions)
    assert len(calls) == 1 and calls[0][1] == checkout and "provisioned" in " ".join(actions)


def test_the_guard_runtime_phase_fails_loudly_for_a_package_environment_that_lacks_a_library(tmp_path, monkeypatch):
    from nexgen_core.guard import GuardRunner

    runner = GuardRunner(vault_data=tmp_path / "vault", home=tmp_path / "home")
    monkeypatch.setattr("nexgen_core.paths.installed_as_package", lambda: True)
    monkeypatch.setattr(runtime, "missing_imports", lambda *_a: ["mcp"])
    with pytest.raises(runtime.RuntimeProvisionError):
        runner._phase_runtime([])


# ---------------------------------------------------------------- the wheel


def _build_lib(project: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "setup.py", "-q", "build_py", "--build-lib", str(out)],
        cwd=project, capture_output=True, text=True, timeout=300, check=False,
    )


@needs_setuptools
def test_the_wheel_carries_the_layer_the_commands_run(tmp_path):
    """`nexgen council` forwarded to a folder that exists only in a clone: the wheel had the
    commands and none of what they run."""
    before = subprocess.run(["git", "status", "--porcelain"], cwd=REAL_VAULT, capture_output=True, text=True).stdout
    out = tmp_path / "lib"

    built = _build_lib(REAL_VAULT, out)

    assert built.returncode == 0, built.stderr[-800:]
    layer = out / "nexgen_core" / "_engine" / "agent-universal-layer"
    for expected in ("council/council.py", "mcp/lazy-mcp.py", "mcp/manifest.yaml", "leak-scan/leak_scan.py",
                     "skills/skills.manifest.yaml.example", "trust/release-signers.txt"):
        assert (layer / expected).is_file(), expected
    assert not (layer / "tests").exists()
    assert not list(layer.rglob("__pycache__"))
    after = subprocess.run(["git", "status", "--porcelain"], cwd=REAL_VAULT, capture_output=True, text=True).stdout
    assert after == before  # building must not litter the repository


@needs_setuptools
def test_a_build_without_the_layer_fails_instead_of_shipping_a_hollow_wheel(tmp_path):
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (project / "setup.py").write_text((REAL_VAULT / "setup.py").read_text(encoding="utf-8"), encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
        "[project]\nname='x'\nversion='1'\n[tool.setuptools]\npackages=['pkg']\n", encoding="utf-8")

    built = _build_lib(project, tmp_path / "lib")

    assert built.returncode != 0
    assert "cannot build" in (built.stderr + built.stdout)
