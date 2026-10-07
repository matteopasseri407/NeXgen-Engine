"""Trying an MCP server: this machine only, behind the gateway, and it ends by itself.

The point of a trial is that nothing is left to find. It is never written to the Vault manifest (so it does not
sync), never written into a CLI's configuration (it is lazy, so the gateway is all there is to clean), and it
stops being served when its time is up without anything being restarted.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))
LAZY_MCP = INFRA / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

from nexgen_core import mcp_add, mcp_placement as mp, mcp_trials  # noqa: E402
from nexgen_core.mcp_plan import build  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({"servers": {
        "lazy-mcp": {"tier": "core", "command": "x"},
        "real": {"exposure": "lazy", "command": "x"},
    }}), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    return manifest


def try_server(name="spike", hours=24.0, **kw):
    kw.setdefault("command", "npx")
    kw.setdefault("args", ["-y", "some-mcp@1.2.3"])
    return mcp_trials.cmd_try(name, targets_raw=kw.pop("targets_raw", "all"), url=kw.pop("url", None), auth_env=None,
                              env_pairs=kw.pop("env_pairs", None), readonly=False, hours=hours, **kw)


def test_a_trial_is_lazy_for_every_cli_and_leaves_the_manifest_alone(env):
    before = env.read_text(encoding="utf-8")
    code, _ = try_server()
    assert code == 0
    assert env.read_text(encoding="utf-8") == before, "a trial must never reach the synced manifest"
    entry = mcp_trials.active_entries()["spike"]
    assert all(mp.place(entry, cli).kind == mp.GATEWAY_KIND for cli in mp.CLIS)


def test_the_state_file_is_private_and_holds_no_secret(env, tmp_path):
    try_server(env_pairs=["TOKEN=${SPIKE_TOKEN}"])
    path = tmp_path / "state" / mcp_trials.FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 if os.name != "nt" else True
    assert "${SPIKE_TOKEN}" in path.read_text(encoding="utf-8")


def test_a_credential_is_refused_like_in_the_manifest(env):
    code, message = try_server(env_pairs=["API_KEY=" + "sk-" + "a" * 30])
    assert code == 2
    assert mcp_trials.active() == {}


def test_a_name_already_in_the_manifest_is_refused(env):
    code, _ = try_server(name="real")
    assert code == 2 and mcp_trials.active() == {}


@pytest.mark.parametrize("hours", [0, -1, mcp_trials.MAX_HOURS + 1])
def test_a_trial_has_a_sane_duration(env, hours):
    assert try_server(hours=hours)[0] == 2


def test_it_expires_by_itself_and_the_overlay_forgets_it(env):
    code, _ = try_server(hours=1)
    assert code == 0
    info = mcp_trials.active()["spike"]
    assert "spike" in mcp_trials.overlay({})
    later = info["expires"] + 1
    assert mcp_trials.active(later) == {}
    assert "spike" not in mcp_trials.overlay({}, later)
    assert mcp_trials.purge_expired(later) == ["spike"]
    assert mcp_trials.purge_expired(later) == []


def test_a_real_server_is_never_shadowed_by_a_trial_of_the_same_name(env):
    mcp_trials.start("ghost", {"command": "x", "exposure": "lazy"}, 1, manifest_names=set())
    merged = mcp_trials.overlay({"ghost": {"command": "real", "exposure": "lazy"}})
    assert merged["ghost"]["command"] == "real"


def test_drop_and_the_listing(env, capsys):
    try_server()
    assert mcp_trials.cmd_trials() == 0
    assert "spike" in capsys.readouterr().out
    assert mcp_trials.cmd_drop("spike")[0] == 0
    assert mcp_trials.cmd_drop("spike")[0] == 1
    assert mcp_trials.active() == {}


def test_promote_writes_a_real_manifest_entry_and_ends_the_trial(env):
    try_server(args=["-y", "some-mcp@1.2.3"])
    code, message = mcp_trials.cmd_promote("spike")
    assert code == 0, message
    declared = yaml.safe_load(env.read_text(encoding="utf-8"))["servers"]["spike"]
    assert declared["exposure"] == "lazy" and declared["command"] == "npx"
    assert not any(key.startswith("_") for key in declared)
    assert mcp_trials.active() == {}
    assert mcp_trials.cmd_promote("spike")[0] == 1


def test_a_corrupt_trial_file_is_no_trials_not_a_crash(env, tmp_path):
    path = tmp_path / "state" / mcp_trials.FILENAME
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert mcp_trials.active() == {} and mcp_trials.purge_expired() == []
    assert try_server()[0] == 0


# --- what each consumer sees ---------------------------------------------------------------------------

def test_the_plan_shows_a_trial_with_its_time_and_the_gateway_serves_it(env):
    try_server()
    plan = build(env)
    row = next(r for r in plan["rows"] if r["server"] == "spike")
    assert row["trial"] and all(cell == "gateway" for cell in row["cells"].values())
    assert all("spike" in served for served in plan["gateway"].values())


def gateway_module(monkeypatch, cli="claude"):
    monkeypatch.setenv("LAZY_MCP_CLI", cli)
    spec = importlib.util.spec_from_file_location("lazy_mcp_for_trials", LAZY_MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gateway_serves_a_trial_and_stops_when_it_ends_without_a_restart(env, monkeypatch):
    gateway = gateway_module(monkeypatch)
    assert "spike" not in gateway._lazy_servers()
    try_server()
    assert "spike" in gateway._lazy_servers()
    mcp_trials.drop("spike")
    assert "spike" not in gateway._lazy_servers()


def test_the_guard_cycle_tidies_expired_trials(env, tmp_path, monkeypatch):
    from nexgen_core.guard import GuardRunner

    try_server(hours=1)
    path = tmp_path / "state" / mcp_trials.FILENAME
    data = json.loads(path.read_text())
    data["trials"]["spike"]["expires"] = 1.0
    path.write_text(json.dumps(data), encoding="utf-8")
    runner = GuardRunner(vault_data=env.parents[3], engine_root=INFRA, home=tmp_path / "home")
    actions: list[str] = []
    runner._phase_mcp(actions, skip_mcp=False)
    assert mcp_trials.active() == {}
    assert any("spike" in a for a in actions)


def test_doctor_reminds_about_a_running_trial_and_is_quiet_without_one(env):
    from nexgen_core.checks.mcp_checks import check_mcp_trials
    from nexgen_core.report import Severity

    assert check_mcp_trials().severity == Severity.OK
    try_server()
    outcome = check_mcp_trials()
    assert outcome.severity == Severity.WARN and "spike" in outcome.message


# --- `mcp add`: lazy and for everyone unless said otherwise ---------------------------------------------

def test_add_is_lazy_and_for_every_cli_by_default(env):
    code, message = mcp_add.add_server("fresh", "all", command="npx", args=["-y", "fresh-mcp@1.0.0"])
    assert code == 0, message
    declared = yaml.safe_load(env.read_text(encoding="utf-8"))["servers"]["fresh"]
    assert declared["exposure"] == "lazy" and sorted(declared["targets"]) == sorted(mp.CLIS)


def test_eager_is_the_explicit_exception(env):
    code, _ = mcp_add.add_server("must-have", "all", command="npx", args=["-y", "x@1.0.0"], lazy=False)
    assert code == 0
    assert yaml.safe_load(env.read_text(encoding="utf-8"))["servers"]["must-have"]["exposure"] == "eager"


def _cli(*argv, env_extra):
    import subprocess

    entry = INFRA / "scripts" / "nexgen_core" / "cli" / "__init__.py"
    return subprocess.run([sys.executable, str(entry), "mcp", *argv], capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, **env_extra}, check=False, timeout=120)


def test_the_command_line_defaults_to_lazy_for_all_and_keeps_the_empty_target_an_error(env, tmp_path):
    extra = {"AGENT_VAULT_DATA": os.environ["AGENT_VAULT_DATA"], "AGENT_STATE_DIR": os.environ["AGENT_STATE_DIR"],
             "NEXGEN_HOME": os.environ["NEXGEN_HOME"]}
    dry = _cli("add", "fresh", "--command", "npx", "--args=-y", "--args=fresh-mcp@1.0.0", "--dry-run", env_extra=extra)
    assert dry.returncode == 0, dry.stderr
    assert '"exposure": "lazy"' in dry.stdout or "exposure: " in dry.stdout
    assert all(cli in dry.stdout for cli in mp.CLIS)
    empty = _cli("add", "fresh", "--command", "npx", "--targets", "", "--dry-run", env_extra=extra)
    assert empty.returncode == 2
    both = _cli("add", "fresh", "--command", "npx", "--eager", "--lazy", "--dry-run", env_extra=extra)
    assert both.returncode == 2


def test_try_and_drop_work_end_to_end_from_the_command_line(env):
    extra = {"AGENT_VAULT_DATA": os.environ["AGENT_VAULT_DATA"], "AGENT_STATE_DIR": os.environ["AGENT_STATE_DIR"],
             "NEXGEN_HOME": os.environ["NEXGEN_HOME"]}
    started = _cli("try", "spike", "--command", "npx", "--args=-y", "--args=some-mcp@1.2.3", "--for", "2", env_extra=extra)
    assert started.returncode == 0, started.stderr
    assert "spike" in _cli("trials", env_extra=extra).stdout
    assert _cli("drop", "spike", env_extra=extra).returncode == 0
    assert "spike" not in _cli("trials", env_extra=extra).stdout


# --- the OAuth trap --------------------------------------------------------------------------------------

def test_an_oauth_only_http_server_behind_the_gateway_is_called_out():
    oauth_http = {"transport": "http", "url": "https://example.com/mcp", "oauth": True, "exposure": "lazy"}
    servers = {"lazy-mcp": {"tier": "core", "command": "x"}, "google": oauth_http}
    found = mp.problems(servers)
    assert any(line.startswith("google:") and "401" in line for line in found)
    with_bearer = {**oauth_http, "auth": {"env": "SOME_TOKEN"}}
    assert mp.problems({**servers, "google": with_bearer}) == []
    eager = {**oauth_http, "exposure": "eager"}
    assert mp.problems({**servers, "google": eager}) == []
