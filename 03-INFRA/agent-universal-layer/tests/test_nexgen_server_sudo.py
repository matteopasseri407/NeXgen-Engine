"""The agents' sudo policy: the file the server role writes, and how it lands.

The file is a permission, so these tests pin three things that are easy to lose:
the list is a ceiling and never grows, nothing is installed before `visudo` has
read it, and a machine where the install cannot escalate is told the one command
an admin runs instead of being left with a half-written policy. Everything runs
through a recorded runner: no `sudo`, no `visudo`, no `/etc`, no home of anybody's.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n, roles, server_sudo  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_processes import RunResult  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.paths import resolve_state_dir  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english_and_private_state(monkeypatch):
    """The sentences asserted here are the source ones, and the state stays in tmp."""
    i18n.set_language("en")
    monkeypatch.delenv("AGENT_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("NEXGEN_HOME", raising=False)
    yield
    i18n.set_language(None)


class Sudo:
    """A recorded machine: which commands ran, and what each one answered."""

    def __init__(self, *, visudo_ok: bool = True, sudo_ok: bool = True, installed: str | None = None,
                 install_ok: bool = True) -> None:
        self.calls: list[list[str]] = []
        self._visudo_ok = visudo_ok
        self._sudo_ok = sudo_ok
        self._installed = installed
        self._install_ok = install_ok

    def __call__(self, cmd: list[str]) -> RunResult:
        self.calls.append(list(cmd))
        if cmd[0] == "visudo":
            return RunResult(0 if self._visudo_ok else 1, "", "" if self._visudo_ok else "syntax error near line 4")
        if cmd[:3] == ["sudo", "-n", "true"]:
            return RunResult(0 if self._sudo_ok else 1, "", "sudo: a password is required")
        if cmd[:3] == ["sudo", "-n", "cat"]:
            if self._installed is None:
                return RunResult(1, "", "cat: No such file or directory")
            return RunResult(0, self._installed, "")
        if cmd[:3] == ["sudo", "-n", "install"]:
            return RunResult(0 if self._install_ok else 1, "", "" if self._install_ok else "install: denied")
        raise AssertionError(f"unexpected command: {cmd}")

    def ran(self, *prefix: str) -> list[list[str]]:
        return [call for call in self.calls if call[:len(prefix)] == list(prefix)]


def candidate_of(home: Path) -> str:
    """The private copy the install writes, spelled the way a command line needs it."""
    return resolve_state_dir(home).joinpath("sudoers", "nexgen-agents").as_posix()


def a_home(tmp_path: Path) -> Path:
    home = tmp_path / "home" / "admin"
    home.mkdir(parents=True)
    return home


# ------------------------------------------------------------------ the list


def test_the_list_is_exactly_the_one_the_server_needs():
    """Widening it is the failure this test exists to make loud."""
    commands = server_sudo.agent_commands("/srv/compose/*", which=lambda name: f"/usr/bin/{name}")

    assert commands == (
        "/usr/bin/docker ps",
        "/usr/bin/docker logs *",
        "/usr/bin/docker restart *",
        "/usr/bin/docker compose -f /srv/compose/* ps",
        "/usr/bin/docker compose -f /srv/compose/* logs *",
        "/usr/bin/docker compose -f /srv/compose/* pull",
        "/usr/bin/docker compose -f /srv/compose/* up -d *",
        "/usr/bin/docker compose -f /srv/compose/* restart *",
        "/usr/bin/systemctl status *",
        "/usr/bin/systemctl restart caddy",
        "/usr/bin/systemctl restart tailscaled",
        "/usr/bin/journalctl *",
        "/usr/bin/apt-get update",
        "/usr/bin/apt-get -y upgrade",
        "/usr/bin/apt list --upgradable",
    )


def test_a_binary_the_machine_does_not_report_falls_back_to_its_plain_path():
    commands = server_sudo.agent_commands("/srv/compose/*", which=lambda name: None)

    assert commands[0] == "/usr/bin/docker ps"
    assert commands[-1] == "/usr/bin/apt list --upgradable"


def test_every_rule_names_the_same_user_and_never_another_one():
    text = server_sudo.render("nexgen", ("/usr/bin/docker ps",))

    rules = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert rules == ["nexgen ALL=(root) NOPASSWD: /usr/bin/docker ps"]


def test_the_file_says_it_is_regenerated_so_a_hand_edit_is_not_a_fix():
    text = server_sudo.render("nexgen", ("/usr/bin/docker ps",))

    assert text.startswith("# Written by the NeXgen Engine server role")
    assert "hand edit here is lost" in text


# --------------------------------------------------------------- the install


def test_the_policy_is_checked_before_anything_privileged_happens(tmp_path):
    home = a_home(tmp_path)
    sudo = Sudo()

    outcome = server_sudo.install(home, "nexgen", runner=sudo, out=lambda _: None, which=lambda n: f"/usr/bin/{n}")

    assert outcome == server_sudo.INSTALLED
    assert sudo.calls[0][:2] == ["visudo", "-cf"]
    installed = sudo.ran("sudo", "-n", "install")
    assert len(installed) == 1
    assert installed[0][3:7] == ["-m", "0440", "-C", candidate_of(home)]
    assert installed[0][-1] == "/etc/sudoers.d/nexgen-agents"
    assert Path(candidate_of(home)).read_text(encoding="utf-8") == server_sudo.render(
        "nexgen", server_sudo.agent_commands(f"{home.as_posix()}/*", which=lambda n: f"/usr/bin/{n}"),
    )


def test_a_file_visudo_refuses_is_never_installed(tmp_path):
    home = a_home(tmp_path)
    sudo = Sudo(visudo_ok=False)
    lines: list[str] = []

    outcome = server_sudo.install(home, "nexgen", runner=sudo, out=lines.append)

    assert outcome == server_sudo.INVALID
    assert sudo.ran("sudo") == []
    assert any("visudo refuses this file" in line for line in lines)
    assert "syntax error" in "\n".join(lines)


def test_the_second_run_finds_the_same_file_and_touches_nothing(tmp_path):
    home = a_home(tmp_path)
    first = Sudo()
    server_sudo.install(home, "nexgen", runner=first, out=lambda _: None, which=lambda n: f"/usr/bin/{n}")
    written = Path(candidate_of(home)).read_text(encoding="utf-8")
    second = Sudo(installed=written)
    lines: list[str] = []

    outcome = server_sudo.install(home, "nexgen", runner=second, out=lines.append, which=lambda n: f"/usr/bin/{n}")

    assert outcome == server_sudo.UNCHANGED
    assert second.ran("sudo", "-n", "install") == []
    assert any("already in force" in line for line in lines)


def test_without_sudo_it_prints_the_file_and_the_one_command_an_admin_runs(tmp_path):
    home = a_home(tmp_path)
    sudo = Sudo(sudo_ok=False)
    lines: list[str] = []

    outcome = server_sudo.install(home, "nexgen", runner=sudo, out=lines.append, which=lambda n: f"/usr/bin/{n}")

    assert outcome == server_sudo.NO_SUDO
    assert sudo.ran("sudo", "-n", "install") == []
    printed = "\n".join(lines)
    assert "NOPASSWD: /usr/bin/docker ps" in printed
    assert f"sudo install -m 0440 -C {candidate_of(home)} /etc/sudoers.d/nexgen-agents" in printed


def test_an_install_that_fails_is_reported_and_not_claimed(tmp_path):
    home = a_home(tmp_path)
    sudo = Sudo(install_ok=False)
    lines: list[str] = []

    outcome = server_sudo.install(home, "nexgen", runner=sudo, out=lines.append)

    assert outcome == server_sudo.FAILED
    assert any("was not installed" in line for line in lines)


def test_the_compose_folder_is_the_home_of_the_admin_installing(tmp_path):
    home = a_home(tmp_path)
    sudo = Sudo()

    server_sudo.install(home, "nexgen", runner=sudo, out=lambda _: None, which=lambda n: f"/usr/bin/{n}")

    written = Path(candidate_of(home)).read_text(encoding="utf-8")
    assert f"compose -f {home.as_posix()}/* ps" in written
    assert "/srv/elsewhere" not in written


# ------------------------------------------------------------------ the role


def test_the_server_role_installs_the_policy_before_configuring_the_gateway(tmp_path):
    calls: list[str] = []

    def sudoers(home: Path, out) -> str:
        calls.append(str(home))
        return server_sudo.INSTALLED

    code = roles.run_server_role(
        root=tmp_path,
        home=tmp_path / "home",
        setup_fn=lambda **kwargs: 0,
        align_fn=lambda: (True, "aligned"),
        problems_fn=lambda: (0, []),
        sudoers_fn=sudoers,
        platform="linux",
        out=lambda _: None,
    )

    assert code == 0
    assert calls == [str(tmp_path / "home")]


@pytest.mark.parametrize("outcome", sorted(server_sudo.FAILURES))
def test_a_policy_that_is_not_in_force_does_not_block_the_gateway_but_fails_the_role(tmp_path, outcome):
    """The machine still gets its gateway; the promise that did not hold stays visible."""
    lines: list[str] = []
    setup_called = []

    code = roles.run_server_role(
        root=tmp_path,
        home=tmp_path / "home",
        setup_fn=lambda **kwargs: setup_called.append(1) or 0,
        align_fn=lambda: (True, "aligned"),
        problems_fn=lambda: (0, []),
        sudoers_fn=lambda home, out: outcome,
        platform="linux",
        out=lines.append,
    )

    assert setup_called == [1]
    assert code == 1
    assert any("the agents' sudo policy is not in force" in line for line in lines)


def test_a_machine_that_is_not_the_server_leaves_the_sudoers_file_alone(tmp_path):
    """The role is Linux-only; on anything else the step never runs."""
    code = roles.run_server_role(
        root=tmp_path,
        home=tmp_path / "home",
        setup_fn=lambda **kwargs: 0,
        align_fn=lambda: (True, "aligned"),
        problems_fn=lambda: (0, []),
        sudoers_fn=lambda home, out: pytest.fail("the step ran on a non-Linux host"),
        platform="win32",
        out=lambda _: None,
    )

    assert code == 0


def test_the_rules_are_written_for_the_admin_who_ran_the_installer(tmp_path, monkeypatch):
    """`sudo sh install.sh` sets SUDO_USER: root is not the user the gateway runs as."""
    monkeypatch.setenv("SUDO_USER", "admin")
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)

    assert roles.installing_user() == "admin"
