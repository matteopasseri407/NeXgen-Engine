"""The doctor's two lines about nodes: which machines are paired, and whether another
PC could reach this gateway at all.

The gateway is asked only when its port answers, so a stopped gateway costs nothing,
and both lines need evidence: a machine with no node is not told that a node could
not reach it, because a loopback gateway on a single-PC install is the normal state
and a laptop outside the house has no other choice. No test runs `openclaw`: the
lister is a fake.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor import collect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.node_checks import check_nodes  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

PINNED = "2026.10.6"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def gateway_config(*, bind: str = "loopback") -> dict:
    return {
        "gateway": {
            "mode": "local", "port": 18789, "bind": bind,
            "auth": {"mode": "token", "token": "a-real-token-value"},
        },
    }


def listening(host: str, port: int, timeout: float) -> bool:
    return True


def silent(host: str, port: int, timeout: float) -> bool:
    return False


NODES = [
    {"nodeId": "a1b2c3d4e5f6", "displayName": "Build Node", "platform": "linux", "connected": True},
    {"nodeId": "f6e5d4c3b2a1", "displayName": "Laptop", "platform": "linux", "connected": False},
]


def by_id(outcomes) -> dict:
    return {outcome.id: outcome for outcome in outcomes}


def test_a_stopped_gateway_is_not_asked_anything():
    asked: list[int] = []

    outcomes = check_nodes(gateway_config(), silent, lambda: asked.append(1) or NODES)

    assert "openclaw.nodes" not in by_id(outcomes)
    assert asked == []


def test_no_lister_means_no_node_line_and_no_query():
    """`nexgen doctor` runs on a schedule: it must not start the harness."""
    outcomes = check_nodes(gateway_config(), listening, None)

    assert "openclaw.nodes" not in by_id(outcomes)
    assert by_id(outcomes)["openclaw.gateway.lan"].severity == Severity.OK


def test_a_listening_gateway_names_the_nodes_it_knows():
    outcomes = check_nodes(gateway_config(), listening, lambda: NODES)

    nodes = by_id(outcomes)["openclaw.nodes"]
    assert nodes.severity == Severity.OK
    assert "Build Node" in nodes.message
    assert "linux" in nodes.message
    assert "connected" in nodes.message
    assert "Laptop" in nodes.message


def test_a_node_that_says_it_is_connected_in_words_is_not_called_offline():
    """The CLI payload may carry `status: "connected"` where the gateway carries a boolean."""
    nodes = [{"nodeId": "a1", "displayName": "Laptop", "platform": "win32", "status": "connected"}]

    message = by_id(check_nodes(gateway_config(), listening, lambda: nodes))["openclaw.nodes"].message

    assert "Laptop (win32, connected)" in message
    assert "not connected" not in message


def test_a_status_that_says_anything_else_is_still_offline():
    nodes = [{"nodeId": "a1", "displayName": "Laptop", "status": "offline"}]

    message = by_id(check_nodes(gateway_config(), listening, lambda: nodes))["openclaw.nodes"].message

    assert "not connected" in message


def test_a_gateway_with_no_node_says_so_plainly():
    outcomes = check_nodes(gateway_config(), listening, lambda: [])

    assert by_id(outcomes)["openclaw.nodes"].message == "No node is paired with this gateway."


def test_a_gateway_that_does_not_answer_about_its_nodes_says_nothing_about_them():
    outcomes = check_nodes(gateway_config(), listening, lambda: None)

    assert "openclaw.nodes" not in by_id(outcomes)
    assert "openclaw.gateway.lan" in by_id(outcomes)


def test_a_node_behind_a_loopback_gateway_is_a_warning_with_the_one_command():
    outcomes = check_nodes(gateway_config(bind="loopback"), listening, lambda: NODES)

    lan = by_id(outcomes)["openclaw.gateway.lan"]
    assert lan.severity == Severity.WARN
    assert "cannot reach it" in lan.message
    assert "nexgen openclaw setup --lan" in (lan.action or "")


def test_a_node_and_a_lan_gateway_pass():
    outcomes = check_nodes(gateway_config(bind="lan"), listening, lambda: NODES)

    assert by_id(outcomes)["openclaw.gateway.lan"].severity == Severity.OK


def test_without_a_node_the_same_sentence_is_information_not_a_warning():
    """A laptop outside the house runs its gateway in loopback on purpose."""
    for lister in (None, lambda: []):
        lan = by_id(check_nodes(gateway_config(bind="loopback"), listening, lister))["openclaw.gateway.lan"]
        assert lan.severity == Severity.OK
        assert "cannot reach it" in lan.message


# --------------------------------------------------------------- through the doctor


def machine(config_path: Path) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version=PINNED, config_path=str(config_path), config_present=True),
        claude=Login(binary=False, login=None),
        codex=Login(binary=False, login=None),
        opencode_go=True,
        local=LocalModels(ollama=()),
        n8n=False,
    )


def run_doctor(tmp_path: Path, *, bind: str, nodes: list[dict] | None, listening_gateway: bool = True):
    config_path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(gateway_config(bind=bind)), encoding="utf-8")
    vault = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "modules.state.yaml").write_text("schema_version: 2\nmodules:\n  openclaw: local\n", encoding="utf-8")
    return collect(
        detected=machine(config_path),
        home=tmp_path / "home",
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        env={"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")},
        platform="linux",
        connect=listening if listening_gateway else silent,
        node_lister=lambda: nodes,
        node_version=lambda: "v24.22.1",
        linger_dir=tmp_path / "linger",
    )


def test_the_doctor_carries_the_nodes_line_when_it_was_asked_for_them(tmp_path):
    outcomes = by_id(run_doctor(tmp_path, bind="lan", nodes=NODES))

    assert "Build Node" in outcomes["openclaw.nodes"].message
    assert outcomes["openclaw.gateway.lan"].severity == Severity.OK


def test_the_doctor_with_a_stopped_gateway_says_nothing_about_nodes(tmp_path):
    outcomes = by_id(run_doctor(tmp_path, bind="loopback", nodes=None, listening_gateway=False))

    assert "openclaw.nodes" not in outcomes
    assert "openclaw.gateway.listening" in outcomes
    assert outcomes["openclaw.gateway.lan"].severity == Severity.OK


def test_the_nodes_flag_is_what_turns_the_question_on():
    from nexgen_core.cli import build_parser

    parser = build_parser()
    openclaw = parser._subparsers._group_actions[0].choices["openclaw"]
    doctor = openclaw._subparsers._group_actions[0].choices["doctor"]

    assert doctor.parse_args([]).nodes is False
    assert doctor.parse_args(["--nodes"]).nodes is True


def test_the_unscheduled_doctor_asks_nobody_anything(tmp_path):
    """`nexgen doctor` runs on the updater's schedule with no lister, and stays passive."""
    config_path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(gateway_config()), encoding="utf-8")
    vault = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "modules.state.yaml").write_text("schema_version: 2\nmodules:\n  openclaw: local\n", encoding="utf-8")

    outcomes = by_id(collect(
        detected=machine(config_path),
        home=tmp_path / "home",
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        env={"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")},
        platform="linux",
        connect=listening,
        node_version=lambda: "v24.22.1",
        linger_dir=tmp_path / "linger",
    ))

    assert "openclaw.nodes" not in outcomes
    assert "openclaw.gateway.lan" in outcomes


def test_a_copy_of_a_home_never_asks_the_gateway_for_its_nodes(tmp_path):
    """On a sandbox, `--nodes` must not probe the machine's own gateway.

    There is one gateway per machine, on a loopback port. A copy of a home that
    probed would report on a process it does not own, and the lister would start
    OpenClaw's CLI against it: the same rule the listening line already follows.
    """
    asked: list[bool] = []
    config_path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(gateway_config(bind="lan")), encoding="utf-8")
    vault = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "modules.state.yaml").write_text("schema_version: 2\nmodules:\n  openclaw: local\n", encoding="utf-8")

    outcomes = by_id(collect(
        detected=machine(config_path),
        home=tmp_path / "home",
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        env={"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")},
        platform="linux",
        node_lister=lambda: asked.append(True) or NODES,
        node_version=lambda: "v24.22.1",
        linger_dir=tmp_path / "linger",
    ))

    assert asked == []
    assert "openclaw.nodes" not in outcomes
    assert "not probed from here" in outcomes["openclaw.gateway.listening"].message
