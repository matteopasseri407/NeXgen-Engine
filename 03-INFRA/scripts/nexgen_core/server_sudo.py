"""The privileged commands the agents may run, as one sudoers file.

One job: render the fixed list of commands the server role's agents need, check it
with `visudo -cf`, and install it under `/etc/sudoers.d` when the installer can
escalate without a prompt. When it cannot, it prints the file and the one command
an admin runs instead: an unattended install that stops to ask for a password is
an install that hangs.

Two rules are not preferences.

The list is a ceiling, one line per granted command. A sudoers file the engine
regenerates is a permission the person cannot audit by remembering what they
wrote, so the file stays exactly as wide as this module and never wider.

The compose folder is the home of the admin installing on this machine, not a
constant. A path that does not exist is a rule that never matches, and the
failure would show up months later as an agent that cannot read its own logs.

Nothing here decides *whether* the server role wants this: `roles.run_server_role`
calls it, and a machine with no sudo at all is told what to run rather than
leaving a half-written policy behind.
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.openclaw_processes import RunResult
from nexgen_core.paths import resolve_state_dir
from nexgen_core.processes import no_window_kwargs

#: The name of the file under `/etc/sudoers.d`. No dot and no tilde: sudo ignores
#: a file named that way, silently.
SUDOERS_NAME = "nexgen-agents"

#: Where a sudoers file is allowed to live on a machine that reads one.
SUDOERS_DIR = Path("/etc/sudoers.d")

#: The rendered file waits here when the install cannot escalate: under the
#: person's own state, where nothing is privileged and nothing is hidden.
CANDIDATE_SUBPATH = ("sudoers", SUDOERS_NAME)

#: The absolute path each command has on a plain Ubuntu, used when `which` cannot
#: answer (no such binary here, or a sandbox with an empty PATH). Sudoers needs an
#: absolute path: a bare name is a syntax error, not a lookup.
DEFAULT_PATHS = {
    "docker": "/usr/bin/docker",
    "systemctl": "/usr/bin/systemctl",
    "journalctl": "/usr/bin/journalctl",
    "apt-get": "/usr/bin/apt-get",
    "apt": "/usr/bin/apt",
}

#: What `install` can say. The caller reads these instead of a bool, because
#: "installed", "already there", "no sudo here" and "the engine wrote something
#: visudo refuses" are four different answers for the person reading the output.
INSTALLED = "installed"
UNCHANGED = "unchanged"
NO_SUDO = "no-sudo"
INVALID = "invalid"

_HEADER = (
    "# Written by the NeXgen Engine server role from its fixed list of privileged",
    "# commands. Every run regenerates this file, so a hand edit here is lost:",
    "# change the engine, not this file.",
)


def _default_runner(cmd: list[str]) -> RunResult:
    """Runs one command and captures it, with no window on a desktop Windows."""
    res = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, **no_window_kwargs(),
    )
    return RunResult(res.returncode, res.stdout, res.stderr)


def _binary(name: str, which: Callable[[str], str | None]) -> str:
    """The absolute path sudoers needs, asked of this machine before it is assumed."""
    return which(name) or DEFAULT_PATHS[name]


def agent_commands(compose_root: str, which: Callable[[str], str | None] = shutil.which) -> tuple[str, ...]:
    """The commands one agent may run as root, exactly as the owner's server needs them.

    `compose_root` is the folder a compose file is read from, with the wildcard
    sudoers needs for "any file in that folder": the admin's home on this machine.
    """
    docker = _binary("docker", which)
    compose = f"{docker} compose -f {compose_root}"
    systemctl = _binary("systemctl", which)
    journalctl = _binary("journalctl", which)
    apt_get = _binary("apt-get", which)
    apt = _binary("apt", which)
    return (
        f"{docker} ps",
        f"{docker} logs *",
        f"{docker} restart *",
        f"{compose} ps",
        f"{compose} logs *",
        f"{compose} pull",
        f"{compose} up -d *",
        f"{compose} restart *",
        f"{systemctl} status *",
        f"{systemctl} restart caddy",
        f"{systemctl} restart tailscaled",
        f"{journalctl} *",
        f"{apt_get} update",
        f"{apt_get} -y upgrade",
        f"{apt} list --upgradable",
    )


def render(user: str, commands: Sequence[str]) -> str:
    """The file's text: what wrote it, then one rule per command.

    One rule per line, not one line with commas: a person auditing this file
    counts privileges by counting lines, and a tag in sudoers applies only to the
    last command of a line.
    """
    rules = [f"{user} ALL=(root) NOPASSWD: {command}" for command in commands]
    return "\n".join([*_HEADER, "", *rules]) + "\n"


def _reason(result: Any) -> str:
    """The first line of whatever the command said, for a message a person reads."""
    said = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
    return said.splitlines()[0] if said else t("exit code {code}", code=getattr(result, "returncode", "?"))


def install(
    home: Path,
    user: str,
    runner: Callable[[list[str]], Any] | None = None,
    out: Callable[[str], None] = print,
    directory: Path = SUDOERS_DIR,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Validate the policy and put it in force, or say what an admin has to run.

    `home` is the installing admin's home: the compose folder comes from it, and
    so does the private copy of the file that waits for a person with sudo. The
    file is checked by `visudo -cf` before anything privileged happens, and the
    installed copy is compared with it so a second run has nothing to do.

    "Can escalate without a prompt" is asked as `sudo -n true`. A machine whose
    NOPASSWD set is narrower than that will be told to run the one command rather
    than have the install guess: asking a person is the safe direction, and it
    always works.
    """
    run = runner or _default_runner
    # POSIX spellings everywhere: these paths name a Linux file, and a command line
    # printed from a Windows checkout must still be the one an admin can paste.
    target = (directory / SUDOERS_NAME).as_posix()
    commands = agent_commands(f"{home.as_posix()}/*", which)
    text = render(user, commands)
    candidate_path = resolve_state_dir(home).joinpath(*CANDIDATE_SUBPATH)
    candidate = candidate_path.as_posix()
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(candidate_path, text, preserve_mode=False)

    checked = run(["visudo", "-cf", candidate])
    if checked.returncode != 0:
        out(t("The agents' sudo policy was not installed: visudo refuses this file ({error}).",
              error=_reason(checked)))
        return INVALID

    if run(["sudo", "-n", "true"]).returncode != 0:
        out(t("This install has no sudo without a password, so the agents' sudo policy was not installed."))
        out(t("The file is written and checked at {path}. As an admin, run this one command:", path=candidate))
        out(f"  sudo install -m 0440 -C {candidate} {target}")
        out(t("The file says:"))
        out(text)
        return NO_SUDO

    in_force = run(["sudo", "-n", "cat", target])
    if in_force.returncode == 0 and in_force.stdout == text:
        out(t("The agents' sudo policy is already in force at {path}: nothing to change.", path=target))
        return UNCHANGED

    written = run(["sudo", "-n", "install", "-m", "0440", "-C", candidate, target])
    if written.returncode != 0:
        out(t("The agents' sudo policy was not installed ({error}).", error=_reason(written)))
        return INVALID
    out(t("The agents' sudo policy is in force at {path}: {count} commands, and nothing else.",
          path=target, count=len(commands)))
    return INSTALLED
