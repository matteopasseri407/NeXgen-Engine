# nexgen: one-job reason="tests for supervisor routing tiers, candidate selection, quota exhaustion and trials"
"""Tests for routing_tiers module and sup model tier integration."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nexgen_local import routing_discovery
from nexgen_local import routing_tiers
from nexgen_local import supervision_policy as policy
from nexgen_local import supervision_state as state
from nexgen_local.supervision_state import SupervisionError


SAMPLE_YAML = """
volume_min_5h: 20000

tiers:
  orchestrator:
    - claude-opus-5-5
    - gpt-6.1-sol
    - gpt-6-astra

  worker:
    - model: opencode-go/deepseek-v4.1-flash
      harness: opencode
      requests_5h: 43330
    - model: gemini-3.8-flash
      harness: antigravity
      requests_5h: 50000
    - model: low-vol/model-flash
      harness: opencode
      requests_5h: 5000

  limited:
    - model: opencode/deepseek-v4.1-flash-free
      harness: opencode
    - model: opencode/glm-5.3-flash-free
      harness: opencode

channels_volume:
  opencode-go/deepseek-v4.1-flash: 43330
  gemini-3.8-flash: 50000
  low-vol/model-flash: 5000
  opencode-go/discovered-flash: 30000
"""


@pytest.fixture
def tier_env(tmp_path, monkeypatch):
    """Temporary test environment for routing tiers and state."""
    v_dir = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer"
    v_dir.mkdir(parents=True, exist_ok=True)
    yaml_file = v_dir / "routing-tiers.yaml"
    yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

    state_file = tmp_path / "state" / "routing-tiers-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("ROUTING_TIERS_PATH", str(yaml_file))
    monkeypatch.setenv("ROUTING_STATE_PATH", str(state_file))
    monkeypatch.setattr(routing_tiers, "probe_cli_inventory", lambda h: set())
    monkeypatch.setattr(routing_discovery, "_run_preflight", lambda m, h: "")

    return SimpleNamespace(yaml_file=yaml_file, state_file=state_file, tmp=tmp_path)


def test_compatibility_without_file(tmp_path, monkeypatch):
    """When routing-tiers.yaml does not exist, existing behaviour is unchanged."""
    missing = tmp_path / "nonexistent" / "routing-tiers.yaml"
    monkeypatch.setenv("ROUTING_TIERS_PATH", str(missing))
    assert not routing_tiers.routing_tiers_exists()
    assert routing_tiers.allowed_for() == ()
    m, h = routing_tiers.resolve_worker_candidate("custom-model", "custom-harness")
    assert m == "custom-model"
    assert h == "custom-harness"

    lane_conf = tmp_path / "config.json"
    lane_conf.write_text(json.dumps({"supervision": {"allowed_models": ["glob/*"]}}), encoding="utf-8")
    assert policy.allowed_models(path=lane_conf) == ("glob/*",)


def test_threshold_volume_worker(tier_env):
    """Candidate with volume below volume_min_5h is not picked for worker tier."""
    picked = routing_tiers.pick("worker")
    assert picked["model"] == "opencode-go/deepseek-v4.1-flash"
    assert picked["requests_5h"] >= 20000


def test_pick_with_exhausted(tier_env):
    """Exhausted model is skipped until TTL expires."""
    routing_tiers.mark_exhausted("opencode-go/deepseek-v4.1-flash", "monthly usage limit reached", ttl_seconds=3600)
    assert routing_tiers.is_exhausted("opencode-go/deepseek-v4.1-flash")

    picked = routing_tiers.pick("worker")
    assert picked["model"] == "gemini-3.8-flash"
    assert picked["harness"] == "antigravity"

    st = routing_tiers.load_state()
    routing_tiers.clean_expired_exhausted(st, now=st["exhausted"]["opencode-go/deepseek-v4.1-flash"]["expires_at"] + 1)
    routing_tiers.save_state(st)
    assert not routing_tiers.is_exhausted("opencode-go/deepseek-v4.1-flash")
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"


def test_model_outside_tier_refused_glm(tier_env):
    """Model outside tier (such as GLM on OpenCode Go) is refused with allowed list."""
    with pytest.raises(SupervisionError) as exc:
        routing_tiers.check_model_allowed("opencode-go/glm-5.3", tier="worker")
    msg = str(exc.value)
    assert "opencode-go/glm-5.3" in msg
    assert "opencode-go/deepseek-v4.1-flash" in msg

    allowed = routing_tiers.allowed_for("worker", allow_limited=False)
    assert "opencode-go/glm-5.3" not in allowed
    assert "opencode-go/deepseek-v4.1-flash" in allowed


def test_quota_exhaustion_detection():
    """Various quota limit messages and status codes are recognized."""
    cases = [
        ("Error: monthly usage limit reached for this key", True),
        ("HTTP 429: quota exceeded for current billing cycle", True),
        ("insufficient_quota on deepseek provider", True),
        ("resource_exhausted: out of credits", True),
        ("plan is spent for the period", True),
        ("SyntaxError: invalid syntax in worker.py", False),
        ("FileNotFoundError: missing 20-brief.md", False),
    ]
    for text, expected in cases:
        detected, _ = routing_tiers.is_quota_exhausted_error(text)
        assert detected is expected, f"Failed for text: {text}"


def test_trial_passes_and_fails_with_fake_adapter():
    """Trial verifies real test passing without touching other files."""
    def passing_runner(model, harness, folder, prompt):
        calc = folder / "calc.py"
        calc.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

    passed, reason = routing_discovery.trial("test-model", "opencode", runner=passing_runner)
    assert passed
    assert reason == "test superato"

    def wrong_runner(model, harness, folder, prompt):
        calc = folder / "calc.py"
        calc.write_text("def add(a: int, b: int) -> int:\n    return a * b\n", encoding="utf-8")

    passed, reason = routing_discovery.trial("test-model", "opencode", runner=wrong_runner)
    assert not passed
    assert "fallire" in reason

    def polluting_runner(model, harness, folder, prompt):
        calc = folder / "calc.py"
        calc.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        (folder / "extra.txt").write_text("unwanted", encoding="utf-8")

    passed, reason = routing_discovery.trial("test-model", "opencode", runner=polluting_runner)
    assert not passed
    assert "file inattesi" in reason


def test_promote_and_rollback(tier_env):
    """Promote places model in first position; rollback restores original order."""
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"

    routing_tiers.promote("gemini-3.8-flash")
    assert routing_tiers.pick("worker")["model"] == "gemini-3.8-flash"

    assert routing_tiers.rollback()
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"


def test_limited_tier_selection(tier_env):
    """Limited models are only picked when explicitly allowed."""
    picked_worker = routing_tiers.pick("worker", allow_limited=False)
    assert picked_worker["model"] == "opencode-go/deepseek-v4.1-flash"

    picked_limited = routing_tiers.pick("worker", allow_limited=True)
    assert picked_limited["model"] == "opencode/deepseek-v4.1-flash-free"
    assert picked_limited["tier"] == "limited"


def _inv(*models: str) -> list[dict[str, str]]:
    """A fake inventory of opencode models."""
    return [{"model": m, "harness": "opencode"} for m in models]


def _no_trial(model, harness):
    raise AssertionError(f"nessun trial atteso per {model}")


def test_discover_first_run_only_records(tier_env):
    """The first discover run records the whole inventory and runs no trial, promotion or alert."""
    phone = MagicMock()
    inv = _inv("opencode-go/discovered-flash", "opencode-go/unknown-vol-flash")
    results = routing_discovery.discover(inventory_fn=lambda: inv, trial_fn=_no_trial, megaphone=phone)

    assert results == []
    assert phone.send_alert.call_count == 0
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"
    st = routing_tiers.load_state()
    assert set(st["known_models"]) == {"opencode-go/discovered-flash", "opencode-go/unknown-vol-flash"}


def test_discover_alerts_new_model_without_promoting(tier_env):
    """A new model that passes the trial is recorded and alerted, never promoted; a second run does not retry it."""
    phone = MagicMock()
    routing_discovery.discover(inventory_fn=lambda: _inv("opencode-go/deepseek-v4.1-flash"),
                               trial_fn=_no_trial, megaphone=phone)

    calls: list[str] = []

    def passing_trial(model, harness):
        calls.append(model)
        assert "opencode-go/discovered-flash" in routing_tiers.load_state()["known_models"]
        return True, "test superato"

    inv = _inv("opencode-go/deepseek-v4.1-flash", "opencode-go/discovered-flash")
    results = routing_discovery.discover(inventory_fn=lambda: inv, trial_fn=passing_trial, megaphone=phone)

    assert [r["status"] for r in results] == ["passed"]
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"
    assert calls == ["opencode-go/discovered-flash"]
    assert phone.send_alert.call_count == 1

    again = routing_discovery.discover(inventory_fn=lambda: inv, trial_fn=_no_trial, megaphone=phone)
    assert again == []
    assert calls == ["opencode-go/discovered-flash"]


def test_discover_alerts_failed_trial_with_reason(tier_env):
    """A new model that fails the trial gets one alert with the reason, and is not promoted."""
    phone = MagicMock()
    routing_discovery.discover(inventory_fn=lambda: _inv("opencode-go/deepseek-v4.1-flash"),
                               trial_fn=_no_trial, megaphone=phone)
    inv = _inv("opencode-go/deepseek-v4.1-flash", "opencode-go/discovered-flash")
    results = routing_discovery.discover(inventory_fn=lambda: inv,
                                         trial_fn=lambda m, h: (False, "tempo limite superato"), megaphone=phone)
    assert results[0]["status"] == "failed: tempo limite superato"
    assert phone.send_alert.call_count == 1
    assert "tempo limite superato" in phone.send_alert.call_args.kwargs["message"]
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"


def test_discover_rollback_is_not_undone(tier_env):
    """After a manual promote and rollback, the next run does not trial the same model again."""
    base = _inv("opencode-go/deepseek-v4.1-flash")
    routing_discovery.discover(inventory_fn=lambda: base, trial_fn=_no_trial, megaphone=MagicMock())
    inv = _inv("opencode-go/deepseek-v4.1-flash", "opencode-go/discovered-flash")
    routing_discovery.discover(inventory_fn=lambda: inv, trial_fn=lambda m, h: (True, "ok"),
                               megaphone=MagicMock())
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"

    routing_tiers.promote("opencode-go/discovered-flash")
    assert routing_tiers.pick("worker")["model"] == "opencode-go/discovered-flash"
    assert routing_tiers.rollback()
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"

    routing_discovery.discover(inventory_fn=lambda: inv, trial_fn=_no_trial, megaphone=MagicMock())
    assert routing_tiers.pick("worker")["model"] == "opencode-go/deepseek-v4.1-flash"


def test_discover_excludes_zen_and_free_channels(tier_env):
    """Zen (opencode/), free and contributor channels are never trialled or alerted."""
    phone = MagicMock()
    base = _inv("opencode-go/deepseek-v4.1-flash")
    routing_discovery.discover(inventory_fn=lambda: base, trial_fn=_no_trial, megaphone=phone)

    inv = _inv("opencode/zen-flash", "opencode-go/free-flash", "opencode-go/contributor-flash")
    results = routing_discovery.discover(inventory_fn=lambda: inv, trial_fn=_no_trial, megaphone=phone)

    assert results == []
    assert phone.send_alert.call_count == 0
    assert routing_discovery.is_flat_model("opencode-go/discovered-flash")
    assert not routing_discovery.is_flat_model("opencode/zen-flash")
    assert not routing_discovery.is_flat_model("opencode-go/free-flash")
    assert not routing_discovery.is_flat_model("opencode-go/contributor-flash")


def test_daily_discover_runs_once_per_day(tier_env, tmp_path):
    """check_and_run_daily_discover runs, then skips for the next 24 hours."""
    first = routing_discovery.check_and_run_daily_discover(
        vault=None, home=tmp_path, megaphone=MagicMock())
    second = routing_discovery.check_and_run_daily_discover(
        vault=None, home=tmp_path, megaphone=MagicMock())
    assert "discovered" in first
    assert "skipped" in second


def test_trial_command_uses_auto_and_stdin_free_launcher(monkeypatch):
    """The opencode trial runs with --auto and the launcher path that Windows resolves."""
    monkeypatch.setattr(routing_discovery.shutil, "which", lambda name: f"C:/bin/{name}.cmd")
    cmd = routing_discovery._trial_command("opencode-go/x-flash", "opencode", "prova")
    assert cmd == ["C:/bin/opencode.cmd", "run", "-m", "opencode-go/x-flash", "--auto", "prova"]


def test_preflight_quota_moves_to_next_candidate(tier_env, monkeypatch):
    """Quota spent on the first candidate: the unit goes to the next one, and the model is marked exhausted."""
    def probe(model, harness):
        return "Error: monthly usage limit reached" if "deepseek" in model else "ok"

    monkeypatch.setattr(routing_discovery, "_run_preflight", probe)
    model, harness = routing_tiers.resolve_worker_candidate()
    assert model == "gemini-3.8-flash"
    assert harness == "antigravity"
    assert routing_tiers.is_exhausted("opencode-go/deepseek-v4.1-flash")


def test_preflight_gives_up_after_every_candidate(tier_env, monkeypatch):
    """When every opencode candidate is spent the loop stops: no endless retries."""
    tier_env.yaml_file.write_text(SAMPLE_YAML.replace("harness: antigravity", "harness: opencode"), encoding="utf-8")
    calls: list[str] = []

    def always_spent(model, harness):
        calls.append(model)
        return "monthly usage limit reached"

    monkeypatch.setattr(routing_discovery, "_run_preflight", always_spent)
    with pytest.raises(SupervisionError):
        routing_tiers.resolve_worker_candidate()
    assert calls == ["opencode-go/deepseek-v4.1-flash", "gemini-3.8-flash"]


def test_preflight_other_failures_do_not_block(tier_env):
    """Only quota messages mark a model; other outcomes and non-opencode harnesses let it through."""
    assert routing_discovery.preflight("opencode-go/x", "opencode", runner=lambda m, h: "ok") == (False, "")
    assert routing_discovery.preflight("gemini-x", "antigravity", runner=lambda m, h: "quota exceeded") == (False, "")
    assert not routing_tiers.is_exhausted("opencode-go/x")


def test_quota_failover_in_run_unit(tmp_path, monkeypatch, tier_env):
    """When unit execution fails due to quota limit, failover picks next candidate."""
    from nexgen_local import supervision_harness as harness

    task_dir = tmp_path / "task_1"
    task_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(state, "open_task", lambda tid: task_dir)
    (task_dir / "20-brief-u1.md").write_text("# brief", encoding="utf-8")

    wt = tmp_path / "worktree_u1"
    wt.mkdir(parents=True, exist_ok=True)
    record = {"unit": "u1", "worktree": str(wt), "mcp": []}
    monkeypatch.setattr(state, "unit_record", lambda tid, u: record)
    monkeypatch.setattr(state, "harness_session_record", lambda tid, h: None)
    monkeypatch.setattr(state, "append_event", lambda *a, **kw: None)

    phone = MagicMock()
    phone.send_alert.return_value = True

    attempts: list[dict] = []

    def mock_open_first_tab(task_id, name, folder, prompt, opener, lister, wait_s, **kw):
        model = kw.get("model", "")
        attempts.append({"model": model, "adapter": kw.get("adapter").name})
        if "deepseek" in model:
            raise SupervisionError("monthly usage limit reached on provider")
        return {"mode": "tab", "unit": name, "model": model, "session": {"id": "s2"}, "window": {}}

    monkeypatch.setattr(harness, "_open_first_tab", mock_open_first_tab)
    monkeypatch.setattr("nexgen_core.megaphone.Megaphone", lambda *a, **kw: phone)

    res = harness.run_unit("task_1", "u1")
    assert len(attempts) == 2
    assert "deepseek" in attempts[0]["model"]
    assert "gemini" in attempts[1]["model"]
    assert res["mode"] == "tab"
    assert routing_tiers.is_exhausted("opencode-go/deepseek-v4.1-flash")


def test_models_cli_commands(tier_env, capsys, monkeypatch):
    """nexgen models CLI: list, pick, trial, promote, rollback, discover."""
    from nexgen_core.cli import models_cmds

    # list
    rc = models_cmds.cmd_models_list(SimpleNamespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Tier worker:" in out
    assert "opencode-go/deepseek-v4.1-flash" in out

    # pick
    rc = models_cmds.cmd_models_pick(SimpleNamespace(tier="worker", limited=False))
    assert rc == 0
    assert "opencode-go/deepseek-v4.1-flash" in capsys.readouterr().out

    rc = models_cmds.cmd_models_pick(SimpleNamespace(tier="worker", limited=True))
    assert rc == 0
    assert "opencode/deepseek-v4.1-flash-free" in capsys.readouterr().out

    # trial
    monkeypatch.setattr(routing_discovery, "trial", lambda m, h: (True, "test superato"))
    rc = models_cmds.cmd_models_trial(SimpleNamespace(model="test-m", harness="opencode"))
    assert rc == 0
    assert "trial superato" in capsys.readouterr().out

    # promote and rollback
    rc = models_cmds.cmd_models_promote(SimpleNamespace(model="gemini-3.8-flash"))
    assert rc == 0
    assert "promosso" in capsys.readouterr().out

    rc = models_cmds.cmd_models_rollback(SimpleNamespace())
    assert rc == 0
    assert "ripristinato" in capsys.readouterr().out

    # discover
    monkeypatch.setattr(routing_discovery, "discover", lambda: [{"model": "m-flash", "status": "promoted"}])
    rc = models_cmds.cmd_models_discover(SimpleNamespace())
    assert rc == 0
    assert "m-flash: promoted" in capsys.readouterr().out
