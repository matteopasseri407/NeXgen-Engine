"""`nexgen openclaw node`: the commands, in order, that make this machine a node.

The module proposes and never runs, so every case here reads the text it returns.
What matters is that OpenClaw's own commands are the ones proposed, that the two
documented routes are both there, that the linger step appears only on a Linux
machine whose lingering is off, and that nothing claims to touch a local gateway.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.node_link import build_node_link, render_node_link  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def commands(link) -> list[str]:
    """Every command in the proposal, in the order a person meets it."""
    return [step.command for route in link.routes for step in route.steps]


def test_the_proposal_opens_with_the_gateway_the_person_named():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    text = render_node_link(link)

    assert "gateway.local:18789" in text
    assert "Nothing has been changed" in text


def test_the_one_paste_route_is_offered_first_because_openclaw_owns_it():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    first = link.routes[0]
    first_commands = [step.command for step in first.steps]

    assert "openclaw devices join-code" in first_commands
    assert "openclaw connect <url> --service --session-host" in first_commands
    assert first_commands.index("openclaw devices join-code") < \
        first_commands.index("openclaw connect <url> --service --session-host")


def test_the_join_code_route_says_that_hosting_sessions_needs_a_trusted_machine():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    connect = next(step for step in link.routes[0].steps
                   if step.command == "openclaw connect <url> --service --session-host")

    assert "shared infrastructure" in (connect.caution or "")


def test_a_magicdns_address_is_accepted_and_named_as_a_tailnet():
    link = build_node_link("server.tailnet-abc.ts.net", platform="linux", user="someone",
                           linger_directory=Path("/nonexistent"))

    assert link.tailnet is True
    assert link.host == "server.tailnet-abc.ts.net"


def test_a_magicdns_node_is_told_the_gateway_publishes_itself_with_server():
    link = build_node_link("server.tailnet-abc.ts.net", platform="linux", user="someone",
                           linger_directory=Path("/nonexistent"))

    assert any("setup --server" in note for note in link.notes)
    assert any("port 443" in note for note in link.notes)
    assert not any("--lan" in note for note in link.notes)


def test_a_magicdns_header_shows_the_https_address_and_no_gateway_port():
    link = build_node_link("server.tailnet-abc.ts.net", platform="linux", user="someone",
                           linger_directory=Path("/nonexistent"))

    text = render_node_link(link)

    assert "gateway at https://server.tailnet-abc.ts.net" in text
    assert "server.tailnet-abc.ts.net:18789" not in text


def test_a_magicdns_node_still_gets_the_explicit_route_with_its_two_approvals():
    link = build_node_link("server.tailnet-abc.ts.net", platform="linux", user="someone",
                           linger_directory=Path("/nonexistent"))

    explicit = [step.command for step in link.routes[1].steps]

    assert "openclaw node install --host server.tailnet-abc.ts.net --port 443 --tls" in explicit
    assert any("belongs to Serve" in note for note in link.notes)


def test_a_node_on_the_home_network_asks_for_no_tls_and_keeps_the_gateway_port():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    explicit = [step.command for step in link.routes[1].steps]

    assert "openclaw node install --host gateway.local --port 18789" in explicit
    assert not any("--tls" in command for command in explicit)


def test_the_join_code_is_named_as_a_single_use_secret():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    join = next(step for step in link.routes[0].steps if step.command == "openclaw devices join-code")

    assert "password" in (join.caution or "")


def test_the_explicit_route_installs_the_service_before_anything_is_approved():
    link = build_node_link("gateway.local", port=19000, name="Laptop", platform="linux",
                           user="someone", linger_directory=Path("/nonexistent"))

    explicit = link.routes[1]
    explicit_commands = [step.command for step in explicit.steps]

    assert explicit_commands[0] == 'openclaw node install --host gateway.local --port 19000 --display-name "Laptop"'
    assert explicit_commands.index("openclaw node install --host gateway.local --port 19000 --display-name \"Laptop\"") < \
        explicit_commands.index("nexgen openclaw pair --approve <device id>")
    assert explicit_commands.index("nexgen openclaw pair") < explicit_commands.index("nexgen openclaw pair --approve <device id>")


def test_the_explicit_route_approves_the_device_before_the_node_request():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    explicit_commands = [step.command for step in link.routes[1].steps]

    assert explicit_commands.index("nexgen openclaw pair --approve <device id>") < \
        explicit_commands.index("nexgen openclaw pair --approve-node <node request id>")


def test_the_device_approval_and_the_node_approval_run_on_the_gateway():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    steps = {step.command: step for route in link.routes for step in route.steps}

    assert steps["nexgen openclaw pair --approve <device id>"].on_gateway is True
    assert steps["nexgen openclaw pair --approve-node <node request id>"].on_gateway is True
    assert steps["openclaw node start"].on_gateway is False


def test_the_node_install_line_omits_a_display_name_that_was_not_given():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    assert "openclaw node install --host gateway.local --port 18789" in commands(link)
    assert not any("--display-name" in command for command in commands(link))


def test_on_linux_without_lingering_the_command_is_proposed(tmp_path):
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=tmp_path)

    assert "loginctl enable-linger someone" in commands(link)


def test_on_linux_with_lingering_already_on_nothing_is_proposed(tmp_path):
    (tmp_path / "someone").write_text("", encoding="utf-8")

    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=tmp_path)

    assert not any("loginctl" in command for command in commands(link))


def test_on_a_machine_without_systemd_lingering_is_never_mentioned(tmp_path):
    link = build_node_link("gateway.local", platform="win32", user="someone", linger_directory=tmp_path)

    assert not any("loginctl" in command for command in commands(link))


def test_the_notes_say_a_local_gateway_is_left_alone():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    text = render_node_link(link)

    assert "left as it is" in text
    assert "one gateway at a time" in text
    assert "Every approval is yours" in text


def test_the_notes_do_not_guess_another_machines_address():
    link = build_node_link("gateway.local", platform="linux", user="someone", linger_directory=Path("/nonexistent"))

    assert any("yours to supply" in note for note in link.notes)


def test_an_empty_address_is_refused():
    with pytest.raises(ValueError):
        build_node_link("   ", platform="linux")


def test_the_proposal_runs_nothing_and_touches_no_file(tmp_path, monkeypatch):
    # Il modulo non deve lanciare niente: se ci provasse, subprocess lo registrerebbe qui.
    launched: list = []

    def forbidden(*args, **kwargs):
        launched.append(args)
        raise AssertionError("the proposal must not run a command")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)

    text = render_node_link(build_node_link("gateway.local", platform="linux", user="someone",
                                            linger_directory=tmp_path))

    assert "openclaw node install" in text
    assert launched == []
