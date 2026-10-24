"""The second pairing stage: the commands a node may run, and the person who approves them.

A node gets in through two requests with different ids. The device request admits
the connection; the node request lists the commands the machine would be allowed to
run on the gateway's behalf, and that is the one this module answers. Every case
here runs a recorder instead of `openclaw` and speaks in the shapes the installed
CLI writes, so nothing touches a gateway, a home or a real device.
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
from nexgen_core.openclaw.setup import run_pair  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_pairing import (  # noqa: E402 - sys.path shim for cloned checkout
    approve_node,
    nodes_pending,
    nodes_status,
)
from nexgen_core.openclaw_service import RunResult  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


class Recorder:
    """The executor the pairing calls go through, answering with canned stdout.

    `pending` is served as a bare JSON array, which is what `openclaw nodes pending
    --json` prints; `status` as the object `nodes status --json` prints.
    """

    def __init__(self, *, devices: list[dict] | None = None, nodes: list[dict] | None = None,
                 status: dict | list | None = None, pending_stdout: str | None = None,
                 status_stdout: str | None = None, approve_code: int = 0):
        self.devices = devices or []
        self.nodes = nodes or []
        self.status = status if status is not None else {"nodes": []}
        self.pending_stdout = pending_stdout
        self.status_stdout = status_stdout
        self.approve_code = approve_code
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        verb = argv[1:3]
        if verb == ["devices", "list"]:
            return RunResult(0, json.dumps({"devices": self.devices}), "")
        if verb == ["nodes", "pending"]:
            text = self.pending_stdout if self.pending_stdout is not None else json.dumps(self.nodes)
            return RunResult(0, text, "")
        if verb == ["nodes", "status"]:
            text = self.status_stdout if self.status_stdout is not None else json.dumps(self.status)
            return RunResult(0, text, "")
        if verb in (["devices", "approve"], ["nodes", "approve"]):
            stderr = "" if self.approve_code == 0 else "unknown id"
            return RunResult(self.approve_code, "", stderr)
        return RunResult(0, "", "")

    def argv_list(self) -> list[list[str]]:
        return [call for call in self.calls]


def node_request(**overrides) -> dict:
    """One pending node request, in the shape the pairing list carries."""
    request = {
        "requestId": "req-9f2",
        "nodeId": "a1b2c3d4e5f6",
        "displayName": "Build Node",
        "platform": "linux",
        "commands": ["system.run", "system.which"],
    }
    request.update(overrides)
    return request


# ------------------------------------------------------------------ the pending list


def test_nodes_pending_asks_the_documented_question_with_json():
    recorder = Recorder(nodes=[node_request()])

    listing = nodes_pending(runner=recorder)

    assert recorder.calls == [["openclaw", "nodes", "pending", "--json"]]
    assert listing["ok"] is True
    assert listing["nodes"][0]["requestId"] == "req-9f2"
    assert listing["nodes"][0]["displayName"] == "Build Node"
    assert listing["nodes"][0]["platform"] == "linux"
    assert listing["nodes"][0]["commands"] == ["system.run", "system.which"]


def test_nodes_pending_reads_the_wrapped_shape_too():
    recorder = Recorder(pending_stdout=json.dumps({"pending": [node_request()], "paired": []}))

    listing = nodes_pending(runner=recorder)

    assert [node["requestId"] for node in listing["nodes"]] == ["req-9f2"]


def test_nodes_pending_never_carries_a_token_field():
    recorder = Recorder(nodes=[node_request(token="SECRET-TOKEN-VALUE", authKey="OTHER-SECRET")])

    listing = nodes_pending(runner=recorder)

    assert "token" not in listing["nodes"][0]
    assert "authKey" not in listing["nodes"][0]
    assert "SECRET-TOKEN-VALUE" not in json.dumps(listing)


def test_nodes_pending_falls_back_to_the_text_table():
    table = (
        "Request   Node         IP           Requested\n"
        "req-9f2   Build Node   gw.local  2m ago\n"
    )
    recorder = Recorder(pending_stdout=table)

    listing = nodes_pending(runner=recorder)

    assert [node["requestId"] for node in listing["nodes"]] == ["req-9f2"]
    assert listing["nodes"][0]["displayName"] == "Build Node"


def test_nodes_pending_with_nothing_waiting_is_an_empty_list():
    listing = nodes_pending(runner=Recorder(pending_stdout="No pending pairing requests."))

    assert listing["ok"] is True
    assert listing["nodes"] == []


def test_nodes_pending_dry_run_runs_nothing():
    recorder = Recorder(nodes=[node_request()])

    listing = nodes_pending(runner=recorder, dry_run=True)

    assert recorder.calls == []
    assert listing["dry_run"] is True
    assert listing["nodes"] == []


# ------------------------------------------------------------------ the approval


def test_approve_node_runs_the_documented_command():
    recorder = Recorder()

    outcome = approve_node("req-9f2", runner=recorder)

    assert recorder.calls == [["openclaw", "nodes", "approve", "req-9f2"]]
    assert outcome["ok"] is True
    assert outcome["request_id"] == "req-9f2"


def test_approve_node_refuses_an_empty_id_without_running_anything():
    recorder = Recorder()

    outcome = approve_node("   ", runner=recorder)

    assert recorder.calls == []
    assert outcome["ok"] is False


# ------------------------------------------------------------------ the known nodes


def test_nodes_status_reads_the_object_the_gateway_prints():
    payload = {"nodes": [
        {"nodeId": "a1b2c3d4e5f6", "displayName": "Build Node", "platform": "linux", "connected": True},
    ]}
    recorder = Recorder(status=payload)

    listing = nodes_status(runner=recorder)

    assert recorder.calls == [["openclaw", "nodes", "status", "--json"]]
    assert listing["ok"] is True
    assert listing["nodes"][0]["displayName"] == "Build Node"
    assert listing["nodes"][0]["connected"] is True


def test_nodes_status_of_an_unreachable_gateway_is_empty_not_a_crash():
    class Silent:
        def __call__(self, argv):
            return RunResult(1, "", "gateway not reachable")

    listing = nodes_status(runner=Silent())

    assert listing["ok"] is False
    assert listing["nodes"] == []


def test_nodes_status_of_an_unreadable_answer_is_empty_not_a_crash():
    recorder = Recorder(status_stdout="not json at all")

    listing = nodes_status(runner=recorder)

    assert listing["nodes"] == []


# ------------------------------------------------------------------ `nexgen openclaw pair`


def test_pair_lists_a_waiting_node_with_its_platform_and_commands():
    recorder = Recorder(nodes=[node_request(token="SECRET-TOKEN-VALUE")])
    lines: list[str] = []

    code = run_pair(runner=recorder, out=lines.append)

    assert code == 0
    output = "\n".join(lines)
    assert "req-9f2" in output
    assert "Build Node" in output
    assert "linux" in output
    assert "system.run" in output and "system.which" in output
    assert "SECRET-TOKEN-VALUE" not in output
    assert not any(call[1:3] == ["nodes", "approve"] for call in recorder.calls)


def test_pair_with_no_node_waiting_says_so():
    lines: list[str] = []

    assert run_pair(runner=Recorder(), out=lines.append) == 0
    assert any("No node is waiting" in line for line in lines)


def test_pair_node_approval_shows_name_platform_and_commands_before_asking():
    recorder = Recorder(nodes=[node_request()])
    lines: list[str] = []
    asked: list[str] = []

    code = run_pair(
        approve_node_id="req-9f2",
        runner=recorder,
        confirm=lambda question: asked.append(question) or True,
        out=lines.append,
    )

    assert code == 0
    assert asked
    assert "Build Node" in asked[0]
    assert "linux" in asked[0]
    assert "system.run" in asked[0]
    assert ["openclaw", "nodes", "approve", "req-9f2"] in recorder.calls
    assert any("approved" in line for line in lines)


def test_pair_node_approval_approves_nothing_when_the_person_declines():
    recorder = Recorder(nodes=[node_request()])
    lines: list[str] = []

    code = run_pair(
        approve_node_id="req-9f2",
        runner=recorder,
        confirm=lambda question: False,
        out=lines.append,
    )

    assert code == 0
    assert not any(call[1:3] == ["nodes", "approve"] for call in recorder.calls)
    assert any("Nothing approved" in line for line in lines)


def test_pair_node_approval_of_an_id_that_is_not_waiting_approves_nothing():
    recorder = Recorder(nodes=[node_request()])
    lines: list[str] = []

    code = run_pair(
        approve_node_id="req-other",
        yes=True,
        runner=recorder,
        out=lines.append,
    )

    assert code == 1
    assert not any(call[1:3] == ["nodes", "approve"] for call in recorder.calls)
    assert any("nothing was approved" in line for line in lines)


def test_pair_node_approval_with_yes_does_not_ask():
    recorder = Recorder(nodes=[node_request()])
    asked: list[str] = []

    code = run_pair(
        approve_node_id="req-9f2",
        yes=True,
        runner=recorder,
        confirm=lambda question: asked.append(question) or False,
        out=lambda line: None,
    )

    assert code == 0
    assert asked == []
    assert ["openclaw", "nodes", "approve", "req-9f2"] in recorder.calls


def test_a_device_approval_does_not_approve_a_node():
    recorder = Recorder(devices=[{"id": "abc123", "summary": "Pixel 8"}], nodes=[node_request()])

    code = run_pair(approve_id="abc123", yes=True, runner=recorder, out=lambda line: None)

    assert code == 0
    assert ["openclaw", "devices", "approve", "abc123"] in recorder.calls
    assert not any(call[1:3] == ["nodes", "approve"] for call in recorder.calls)


def test_the_two_stages_are_approved_in_order_when_both_are_asked_for():
    recorder = Recorder(devices=[{"id": "abc123"}], nodes=[node_request()])

    code = run_pair(
        approve_id="abc123",
        approve_node_id="req-9f2",
        yes=True,
        runner=recorder,
        out=lambda line: None,
    )

    assert code == 0
    approvals = [call for call in recorder.calls if call[1:3] in (["devices", "approve"], ["nodes", "approve"])]
    assert approvals == [
        ["openclaw", "devices", "approve", "abc123"],
        ["openclaw", "nodes", "approve", "req-9f2"],
    ]


def test_pair_lists_the_nodes_already_paired_with_their_state():
    recorder = Recorder(status={"nodes": [
        {"nodeId": "a1b2c3d4e5f6", "displayName": "Build Node", "platform": "linux", "connected": True},
    ]})
    lines: list[str] = []

    assert run_pair(runner=recorder, out=lines.append) == 0
    output = "\n".join(lines)
    assert "Nodes already paired" in output
    assert "Build Node" in output
    assert "connected" in output


def test_pair_with_a_failed_node_approval_is_a_non_zero_code():
    recorder = Recorder(nodes=[node_request()], approve_code=1)
    lines: list[str] = []

    code = run_pair(approve_node_id="req-9f2", yes=True, runner=recorder, out=lines.append)

    assert code == 1
    assert any("failed" in line for line in lines)
