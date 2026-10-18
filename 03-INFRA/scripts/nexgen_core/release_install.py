"""What `release ship --install` does to this machine: update, prove it, roll back.

The release is not finished when the tag is published: the machine that runs it
has to run the new engine too. This is that half, kept apart from the traphila in
`release_ship.py` — run the installed launcher's `update`, prove the installed
engine with its own smoke and the interpreter the launchers use, put the machine
back on the version it came from when the smoke fails, and report a vault that
cannot be aligned instead of hiding it behind "already at version".

The `run` seam is the same one ship uses: a callable that answers like the
commands do, so every step here is tested without touching a real machine.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess

from .files import atomic_write_text
from .module_install import shim_path
from .paths import resolve_home, resolve_runtime_dir
from .release import newer_version
from .shims import PRIMARY

#: The updater's own refusal when a repository it merges into is not clean
#: (`_assert_clean` in `nexgen_core/updater.py`, same git flags here).
DIRTY_REFUSAL = re.compile(r"(?P<label>\w+) repository is dirty")
DEFAULT_INSTALL_LOG = "nexgen-release-ship-install.log"


class InstallProblem(RuntimeError):
    """Something this machine could not finish; the caller turns it into its own stop line."""


def _reported_folders(output: str) -> dict[str, Path]:
    """The engine and data folders the updater prints at the start of every run."""
    return {match.group(1).lower(): Path(match.group(2).strip())
            for match in re.finditer(r"^(Engine|Data):\s+(\S.*?)\s*$", output, re.M)}


@dataclass
class Installer:
    """This machine's half of a release: the install step of `release ship`."""

    engine_root: Path
    repo: Path
    run: Callable[[Sequence[str], Path], CompletedProcess[str]]
    log: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep
    poll_seconds: float = 30.0
    install_log: Path | None = None

    def installed_version(self) -> str:
        """The version this machine's engine tree says it is, or `?` when there is no tree."""
        installed_file = self.engine_root.parent / "VERSION"
        return installed_file.read_text(encoding="utf-8").strip() if installed_file.is_file() else "?"

    def install(self, version: str) -> None:
        """Bring this machine to `version`, prove it, and leave it on something that runs."""
        previous = self.installed_version()
        self._channel = self.channel()
        output = self._update(version, previous)
        installed = self.installed_version()
        # A machine on stable lands on the release itself; one on nightly or beta
        # follows its channel, whose commits already carry the release or more.
        landed = installed == version or (self._channel != "stable" and installed != "?"
                                           and newer_version(installed, version))
        if not landed:
            raise InstallProblem(f"this machine is still on {installed}, not {version}")
        if (dirty := self._data_with_dirty_files(output)):
            raise InstallProblem(f"the vault {dirty} has uncommitted or untracked files: "
                                 "commit or stash them, then run ship --install again")
        self._smoke(version, previous)
        doctor = self.run([str(self.launcher()), "doctor", "--summary"], self.repo)
        if doctor.returncode != 0:
            last = (doctor.stdout or doctor.stderr).strip().splitlines()
            raise InstallProblem(f"installed, but doctor is not clean: {last[-1] if last else doctor.returncode}")

    def launcher(self) -> Path:
        """The installed command, the one that runs the engine this machine has.

        Ship runs from a development checkout, whose own `nexgen` is what PATH
        resolves while it runs: that one answers "already at the requested released
        version" about the checkout, so v2.16.3 updated nothing and left this machine
        on 2.16.2 with the release green. Every command here goes through the
        launcher the installer wrote, and a machine without one stops the release
        instead of quietly updating another tree.
        """
        launcher = shim_path(resolve_home() / ".local" / "bin", PRIMARY)
        if not launcher.is_file():
            raise InstallProblem(f"the installed command is not at {launcher}: run 'nexgen init' from the "
                                 "engine you want to use (it rewrites the launchers), then ship --install again")
        return launcher

    def channel(self) -> str:
        """The update channel the installed engine follows, read from its own `--version`.

        `nexgen --version` prints `NeXgen Engine X.Y.Z (<channel> ...)`; anything
        unreadable is stable, the channel a release tag belongs to.
        """
        answer = self.run([str(self.launcher()), "--version"], self.repo)
        match = re.search(r"\((stable|nightly|beta)\b", answer.stdout or "")
        return match.group(1) if match else "stable"

    def _update(self, version: str, previous: str) -> str:
        """Run the updater; a rollback caused by a colliding cycle is retried once, and only once.

        On stable the release tag is named. A machine on nightly or beta refuses a
        named tag (v2.16.5 stopped there), so it updates along its own channel.
        """
        argv = [str(self.launcher()), "update", "--yes"]
        if getattr(self, "_channel", "stable") == "stable":
            argv += ["--target", version]
        for attempt in (1, 2):
            update = self.run(argv, self.repo)
            output = f"{update.stdout}\n{update.stderr}"
            if update.returncode == 0:
                return output
            if (refusal := self._dirty_refusal(output)):
                raise InstallProblem(refusal)
            log = self._log_path()
            atomic_write_text(log, output)
            # A clean rollback leaves the previous version in place. The usual
            # cause is a scheduled guard or sync cycle that started in the same
            # minute, so one more try after it has finished is worth it; a
            # half-done rollback is never retried.
            if attempt == 2 or self.installed_version() != previous:
                raise InstallProblem(f"nexgen update failed and was rolled back; full output in {log}")
            self.log(f"   update failed and was rolled back, retrying once (output in {log})")
            self.sleep(self.poll_seconds * 4)
        raise InstallProblem(f"nexgen update did not finish for v{version}")  # the loop returns or raises above

    def _log_path(self) -> Path:
        return self.install_log or Path(tempfile.gettempdir()) / DEFAULT_INSTALL_LOG

    def _smoke(self, version: str, previous: str) -> None:
        """The installed engine's own smoke, run the way the machine runs it.

        A machine that fails it goes back to the version it came from, by the same
        update command, and the release stops there saying which version it is on.
        """
        script = self.engine_root / "scripts" / "smoke.py"
        if not script.is_file():
            raise InstallProblem(f"the installed engine has no {script.name}: nothing proved it runs")
        smoke = self.run([*self.launcher_python(), str(script)], self.repo)
        if smoke.returncode == 0:
            self.log(f"   the installed v{version} passes its own smoke")
            return
        log = self._log_path()
        atomic_write_text(log, f"{smoke.stdout}\n{smoke.stderr}")
        if getattr(self, "_channel", "stable") != "stable":
            # A moving channel has no release tag to go back to by name: the way back
            # is the stable channel, which the owner chooses, not ship.
            raise InstallProblem(f"the installed engine on {self._channel} failed its smoke (output in {log}); "
                                 "run 'nexgen update --yes --channel stable' to go back to the last release")
        rollback = self.run([str(self.launcher()), "update", "--yes", "--target", previous], self.repo)
        if rollback.returncode != 0 or self.installed_version() != previous:
            raise InstallProblem(f"the installed v{version} failed its smoke (output in {log}) and this machine "
                                 f"is not back on {previous}: run 'nexgen update --yes --target {previous}' by hand")
        raise InstallProblem(f"the installed v{version} failed its smoke (output in {log}); "
                             f"this machine is back on {previous}")

    def _runs_this_engine(self, argv: list[str]) -> bool:
        """True when this interpreter is Python 3.11+, the floor the engine declares (the launchers' own check)."""
        probe = self.run([*argv, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"], self.repo)
        return probe.returncode == 0

    def launcher_python(self) -> list[str]:
        """The interpreter the installed launchers use, as the argv they use it with.

        The same order they follow: the provisioned runtime, then `py -3` and
        `python` on Windows or `python3` and `python` elsewhere, each only if it
        really is a Python this engine can run on. Never the checkout's
        interpreter: a release tree carries no `.venv` of its own.
        """
        runtime = resolve_runtime_dir(resolve_home())
        if (runtime / ".provisioned").is_file():
            for candidate in (runtime / "bin" / "python", runtime / "Scripts" / "python.exe"):
                if candidate.is_file():
                    return [str(candidate)]
        names = (("py", "-3"), ("python",)) if sys.platform == "win32" else (("python3",), ("python",))
        for name in names:
            found = shutil.which(name[0])
            if found and self._runs_this_engine([found, *name[1:]]):
                return [found, *name[1:]]
        raise InstallProblem("no Python 3.11 or newer found to run the installed engine's smoke")

    def _dirty_refusal(self, output: str) -> str | None:
        """The updater's refusal for a repository that is not clean, in one line naming the folder.

        Nothing was rolled back and nothing will be on a second try: the files a
        person must commit or stash are the whole reason, so the release stops here.
        """
        match = DIRTY_REFUSAL.search(output)
        if match is None:
            return None
        label = match.group("label")
        folder = _reported_folders(output).get("data" if label == "data" else "engine")
        return (f"the {label} checkout has uncommitted or untracked files ({folder or '?'}): "
                "commit or stash them, then run ship --install again")

    def _data_with_dirty_files(self, output: str) -> Path | None:
        """The data folder the updater named, when it still carries uncommitted or untracked files.

        `nexgen update` refuses on that before it moves the engine, but it returns
        before looking when the engine is already at the wanted version: the release
        ended green on a machine whose vault cannot be aligned. The same question
        is asked here, with the same git flags.
        """
        folder = _reported_folders(output).get("data")
        if folder is None or not folder.is_dir():
            return None
        status = self.run(["git", "-C", str(folder), "status", "--porcelain=v1", "--untracked-files=normal"],
                          self.repo)
        return folder if status.returncode == 0 and status.stdout.strip() else None
