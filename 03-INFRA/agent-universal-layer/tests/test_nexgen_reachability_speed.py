"""The reachability check asks every connector at once, not one after another.

Each probe waits on the network with a short per-server timeout. Asked in
sequence, three unreachable connectors spent three timeouts inside a single
check, and `doctor --summary` has one overall budget: the sum is what pushed it
over. The answer itself must not change, and the report must keep the order it
chose rather than the order the network answered in.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks import reachability_checks  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

MANIFEST = """
schema_version: 1
servers:
{servers}
"""

SERVER = """  {name}:
    transport: http
    tier: core
    url: "http://127.0.0.1:{port}/"
    require_env: PROBE_ENABLED
    targets: [claude]
"""


def _write_manifest(vault: Path, names: list[str]) -> None:
    servers = "".join(SERVER.format(name=name, port=60000 + index) for index, name in enumerate(names))
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(MANIFEST.format(servers=servers), encoding="utf-8")


def test_four_silent_servers_cost_one_wait_not_four(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_ENABLED", "1")
    names = ["alpha", "beta", "gamma", "delta"]
    vault, home = tmp_path / "vault", tmp_path / "home"
    _write_manifest(vault, names)

    def silent(url: str, timeout: float):
        time.sleep(0.2)
        return Severity.UNDETERMINED, "nothing listening"

    monkeypatch.setattr(reachability_checks, "_probe_tcp", silent)

    started = time.monotonic()
    outcomes = reachability_checks.check_mcp_reachability(vault, home, timeout=0.2)
    spent = time.monotonic() - started

    assert [outcome.id for outcome in outcomes] == [
        f"mcp.reachable.{name}" for name in sorted(names)
    ]
    assert all(outcome.severity == Severity.UNDETERMINED for outcome in outcomes)
    assert spent < 0.5, spent


def test_every_server_still_gets_its_own_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_ENABLED", "1")
    vault, home = tmp_path / "vault", tmp_path / "home"
    _write_manifest(vault, ["silent", "answering"])

    def probe(url: str, timeout: float):
        if "60001" in url:
            return Severity.OK, "127.0.0.1:60001 responds"
        return Severity.UNDETERMINED, "nothing listening"

    monkeypatch.setattr(reachability_checks, "_probe_tcp", probe)

    by_id = {o.id: o for o in reachability_checks.check_mcp_reachability(vault, home, timeout=0.2)}

    assert by_id["mcp.reachable.answering"].severity == Severity.OK
    assert by_id["mcp.reachable.silent"].severity == Severity.UNDETERMINED
