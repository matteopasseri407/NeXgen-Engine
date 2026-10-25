"""Una sessione appartiene alla sua copia, i ruoli scelgono l'harness, il locale riceve un piano.

Queste prove usano solo cartelle temporanee e finestre finte: nessun harness vero
viene avviato. La seconda unità dello stesso compito è il caso che il 2026-10-21
ha rotto la corsa h5: la sessione di h4 non deve ricevere il lavoro di h5.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nexgen_local import cli
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_policy as policy
from nexgen_local import supervision_scout as scout
from nexgen_local import supervision_state as state
from nexgen_local.cmds import sup as sup_cmd
from nexgen_local.supervision_costs import SessionFacts


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    (tmp_path / "lane").mkdir(parents=True, exist_ok=True)
    return tmp_path / "lane"


@pytest.fixture
def task(lane):
    return state.start("prova affinità")["id"]


def _unit(lane, task: str, name: str):
    folder = lane / "supervision" / task / f"23-unita-{name}"
    folder.mkdir(parents=True)
    (lane / "supervision" / task / f"20-brief-{name}.md").write_text(
        f"# Brief\n\ncontenuto segreto di {name}\n", encoding="utf-8",
    )
    state.append_event(
        task, "unit_created", unit=name, worktree=str(folder), branch=f"sup/{task}/{name}",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(lane),
    )
    return folder


class Opener:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, title, argv, *, cwd, marker=""):
        self.calls.append({"title": title, "argv": list(argv), "cwd": cwd, "marker": marker})
        return {"title": title, "launcher": "finto", "pid": 4242}


def test_a_second_unit_never_reuses_the_session_of_another_copy(task, lane, monkeypatch):
    first = _unit(lane, task, "prima")
    second = _unit(lane, task, "seconda")
    state.append_event(task, "attached", unit="prima", role="esecutore", harness="opencode",
                       session="ses_prima", folder=str(first))
    opener = Opener()
    sessions = [{"id": "ses_seconda", "directory": str(second), "created": 10**13}]
    monkeypatch.setattr(harness, "_opencode_executable", lambda: "oc-finto")
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: {"session": "ses_seconda"})

    result = harness.run_unit(task, "seconda", opener=opener, lister=lambda: sessions, wait_s=0)

    assert len(opener.calls) == 1, "una scheda nuova nella copia giusta"
    assert opener.calls[0]["argv"][1] == str(second)
    assert opener.calls[0]["cwd"] == second
    assert result["session"]["id"] == "ses_seconda"
    events = [row["event"] for row in state.read_events(task)]
    assert "harness_session_foreign" in events
    foreign = [row for row in state.read_events(task) if row["event"] == "harness_session_foreign"][-1]
    assert foreign["session"] == "ses_prima"
    assert foreign["session_folder"] == str(first)
    assert foreign["copy"] == str(second)
    assert "nata in un'altra copia" in foreign["reason"]


def test_a_legacy_session_without_a_folder_stays_reusable(task, lane, monkeypatch):
    folder = _unit(lane, task, "vecchia")
    state.append_event(task, "attached", unit="vecchia", role="esecutore", harness="opencode",
                       session="ses_legacy")
    opener = Opener()
    delivered: list = []
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: {"session": "ses_legacy"})

    result = harness.run_unit(
        task, "vecchia", opener=opener,
        runner=lambda session, prompt, directory, model="": delivered.append(session) or SimpleNamespace(pid=9),
        measure=lambda session: SessionFacts(outcome="idle", idle_ms=10, updated_ms=10),
        wait_s=0,
    )

    assert opener.calls == [], "la sessione esiste: nessuna scheda nuova"
    assert delivered == ["ses_legacy"]
    assert result["mode"] == "run"
    event = [row for row in state.read_events(task) if row["event"] == "unit_started"][-1]
    assert event["session"] == "ses_legacy" and event["folder"] == str(folder)


def test_the_roles_default_and_then_follow_the_lane(task, lane):
    assert policy.roles() == {"esecutore": "opencode", "ricognitore": "agy"}
    (lane / "config.json").write_text(
        json.dumps({"supervision": {"roles": {"esecutore": "claude", "ricognitore": "opencode"}}}),
        encoding="utf-8",
    )

    assert policy.harness_for("esecutore") == "claude"
    assert policy.harness_for("ricognitore") == "opencode"
    assert "esecutore claude" in sup_cmd._roles_line()
    assert "ricognitore opencode" in sup_cmd._roles_line()


def test_a_role_naming_an_unknown_harness_is_refused_before_a_tab_opens(lane):
    (lane / "config.json").write_text(
        json.dumps({"supervision": {"roles": {"esecutore": "inventato"}}}), encoding="utf-8",
    )

    with pytest.raises(policy.PolicyError, match="harness sconosciuto"):
        policy.roles()


def test_the_run_verb_passes_the_chosen_harness_only_when_the_owner_names_it(monkeypatch):
    seen: list[dict] = []

    def fake_run(task_id, unit, **kwargs):
        seen.append({"task": task_id, "unit": unit, **kwargs})
        return {"mode": "tab", "window": {}, "session": None, "unit": unit}

    monkeypatch.setattr(harness, "run_unit", fake_run)

    sup_cmd._run_unit(SimpleNamespace(id="t", unit="u", harness=""))
    sup_cmd._run_unit(SimpleNamespace(id="t", unit="u", harness="claude"))

    assert seen[0] == {"task": "t", "unit": "u"}
    assert seen[1] == {"task": "t", "unit": "u", "harness": "claude"}


def test_the_run_verb_passes_the_model_only_when_the_owner_names_it(monkeypatch):
    seen: list[dict] = []

    def fake_run(task_id, unit, **kwargs):
        seen.append({"task": task_id, "unit": unit, **kwargs})
        return {"mode": "tab", "window": {}, "session": None, "unit": unit}

    monkeypatch.setattr(harness, "run_unit", fake_run)

    sup_cmd._run_unit(SimpleNamespace(id="t", unit="u", harness="agy", model=""))
    sup_cmd._run_unit(SimpleNamespace(id="t", unit="u", harness="agy", model="gemini-3.8-flash"))

    assert seen[0] == {"task": "t", "unit": "u", "harness": "agy"}
    assert seen[1] == {"task": "t", "unit": "u", "harness": "agy", "model": "gemini-3.8-flash"}


def test_the_run_parser_offers_the_model_flag(capsys):
    with pytest.raises(SystemExit):
        cli.main(["sup", "run", "--help"])

    out = capsys.readouterr().out
    assert "--model" in out


def test_the_scout_verb_accepts_a_harness_and_resolves_it_before_the_copy(lane):
    (lane / "config.json").write_text(
        json.dumps({"supervision": {"roles": {"ricognitore": "opencode"}}}), encoding="utf-8",
    )

    assert scout._scout_harness("") == "opencode"
    assert scout._scout_harness("claude") == "claude"
    with pytest.raises(scout.ScoutError, match="harness sconosciuto"):
        scout._scout_harness("inventato")


def test_the_scout_parser_offers_the_harness_flag(lane, capsys):
    with pytest.raises(SystemExit):
        cli.main(["sup", "scout", "--help"])

    out = capsys.readouterr().out
    assert "--harness" in out
    assert "--in-scheda" not in out and "--round" not in out


def test_agy_as_executor_opens_a_tab_and_says_the_session_stays_manual(task, lane, monkeypatch, tmp_path):
    """agy can be followed now, so the note says what happened, not that it is blind.

    The store is a fake folder of this test's own: the engine reads agy's
    conversations read-only, and no test may reach the one this machine has.
    """
    _unit(lane, task, "prova")
    opener = Opener()
    monkeypatch.setattr(harness.shutil, "which", lambda name: f"{name}-finto")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "antigravity"))

    result = harness.run_unit(task, "prova", harness="agy", opener=opener, wait_s=0)

    argv = opener.calls[0]["argv"]
    assert argv[0] == "agy-finto"
    assert argv[1:4] == ["--mode", "accept-edits", "--prompt-interactive"]
    assert result["session"] is None
    assert result["harness"] == "agy"
    assert "nessuna sessione di agy è comparsa" in result["reason"]
    assert "aggancio a mano" in result["reason"]


def test_claude_opens_a_tab_with_the_id_the_engine_itself_chose(task, lane, monkeypatch, tmp_path):
    _unit(lane, task, "prova")
    opener = Opener()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(harness.shutil, "which", lambda name: f"{name}-finto")

    result = harness.run_unit(task, "prova", harness="claude", opener=opener, wait_s=0)

    argv = opener.calls[0]["argv"]
    assert argv[0] == "claude-finto"
    assert argv[1] == "--session-id"
    assert argv[3:5] == ["--permission-mode", "acceptEdits"]
    assert "--bypassPermissions" not in argv
    assert result["harness"] == "claude"
    assert result["session"] is None, "nessun transcript in questa macchina finta"


def test_a_local_unit_gets_a_step_by_step_plan_and_two_tests_warning(tmp_path):
    brief = tmp_path / "20-brief-prova.md"
    brief.write_text("# Brief\n", encoding="utf-8")

    local = harness.prompt_text("20261022-000000-prova", "prova", brief, harness="local")
    normal = harness.prompt_text("20261022-000000-prova", "prova", brief, harness="opencode")

    assert "piano passo per passo" in local
    assert "già scritti e rossi" in local and "non modificarli" in local
    assert str(tmp_path / "30-resoconto-prova.md") in local
    assert "piano passo per passo" not in normal
    assert "contenuto segreto" not in local
