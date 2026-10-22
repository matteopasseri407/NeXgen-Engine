"""I permessi dell'esecutore e lo stallo: il profilo scritto prima, le risposte dopo.

La copia è un repository git vero, perché quello che si prova è proprio il
confine: il file di configurazione non deve entrare nel diff. L'harness è finto e
restituisce JSON letterali: nessuna sessione vera viene interrogata.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_permissions as permissions
from nexgen_local import supervision_policy as policy
from nexgen_local import supervision_stall as stall
from nexgen_local import supervision_state as state

from supervision_repo import git_in


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """La lane in una cartella temporanea: nessun test tocca lo stato vero."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


@pytest.fixture
def task(lane):
    return state.start("prova permessi")["id"]


@pytest.fixture
def copy(tmp_path):
    """Una copia dell'unità che è un repository git, con un file già tracciato."""
    folder = tmp_path / "unita"
    git_in(tmp_path, "init", "-q", "-b", "main", str(folder))
    git_in(folder, "config", "user.email", "t@example.com")
    git_in(folder, "config", "user.name", "t")
    git_in(folder, "config", "commit.gpgsign", "false")
    (folder / "README.md").write_text("# prova\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "base")
    return folder


# --- il profilo scritto prima che la scheda si apra -------------------------


def test_the_profile_allows_the_copy_by_relative_path_and_denies_the_absolute_forms(task, copy):
    """La copia si raggiunge per percorso relativo; fuori restano solo le forme negate.

    Una scheda vera ha chiesto `docs/a.md` per un file della copia e
    `C:/.../fuori/vietato.txt` per uno fuori: due forme diverse, quindi due
    gruppi di regole. Vince l'ultima che combacia, e la cartella del compito è
    l'unica forma assoluta riaperta dopo le negazioni.
    """
    task_folder = state.open_task(task)
    edit = [rule for rule in permissions.profile_rules(copy, task_folder) if rule["action"] == "edit"]

    assert edit[0] == {"action": "edit", "resource": "*", "effect": "allow"}
    assert edit[1] == {"action": "edit", "resource": "?:/*", "effect": "deny"}
    assert edit[2] == {"action": "edit", "resource": "/*", "effect": "deny"}
    assert edit[3] == {"action": "edit", "resource": "../*", "effect": "deny"}
    assert edit[4] == {
        "action": "edit", "resource": task_folder.resolve().as_posix() + "/*", "effect": "allow",
    }


def _absolute_edit_allowances(rules: list[dict]) -> list[str]:
    """Le regole di modifica che nominano un percorso assoluto, in qualunque forma.

    Un percorso assoluto ha una lettera di unità (`C:/...`) oppure comincia dalla
    radice (`/tmp/...`): la prima forma è quella di Windows, la seconda quella di
    Linux. Il filtro deve riconoscerle entrambe; le regole di diniego restano
    fuori perché chiedono `effect == "allow"`.
    """
    return [
        str(rule["resource"]) for rule in rules
        if rule["action"] == "edit" and rule["effect"] == "allow"
        and (str(rule["resource"]).startswith("/") or ":" in str(rule["resource"]))
    ]


def test_the_only_absolute_edit_allowance_is_the_task_folder(task, copy):
    """`<copia>/*` non combaciava con niente: nessun permesso torna assoluto per la copia."""
    rules = permissions.profile_rules(copy, state.open_task(task))

    assert _absolute_edit_allowances(rules) == [
        state.open_task(task).resolve().as_posix() + "/*",
    ]


def test_the_absolute_allowance_is_recognized_on_a_posix_task_folder(monkeypatch):
    """Su Linux la cartella del compito è `/tmp/.../*`: il filtro la vede come assoluta."""
    class PosixPath:
        """Un percorso POSIX che il sistema che esegue il test non canonicalizza."""

        def __init__(self, value):
            self.value = str(value)

        def resolve(self):
            return self

        def as_posix(self):
            return self.value

    monkeypatch.setattr(permissions, "Path", PosixPath)

    rules = permissions.profile_rules("/casa/copia", "/tmp/casa/compito")

    assert _absolute_edit_allowances(rules) == ["/tmp/casa/compito/*"]


def test_a_command_is_allowed_unless_it_pushes(task):
    """Il profilo non approva in blocco: non esiste una regola che dica sì a ogni comando."""
    rules = permissions.profile_rules("C:/copia", "C:/compito")
    shell = [rule for rule in rules if rule["action"] == "shell"]

    assert shell and all(rule["effect"] == "deny" for rule in shell)
    assert not any(rule["resource"] == "*" for rule in shell)


def test_the_file_is_written_and_the_owner_keys_are_kept(task, copy):
    (copy / permissions.CONFIG_FILE).write_text(
        json.dumps({"$schema": "https://opencode.ai/config.json", "model": "provider/modello"}),
        encoding="utf-8",
    )

    target = permissions.write_profile(task, copy)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["model"] == "provider/modello", "il modello scelto dal proprietario resta"
    assert payload["$schema"] == "https://opencode.ai/config.json"
    assert payload["permissions"] == permissions.profile_rules(copy, state.open_task(task))


def test_a_declared_local_model_is_written_under_its_provider(task, copy):
    target = permissions.write_profile(
        task, copy, model="locale/qwen3.6-35b-a3b",
        models={"locale": {"qwen3.6-35b-a3b": {"name": "qwen (locale)", "tool_call": True}}},
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["model"] == "locale/qwen3.6-35b-a3b"
    assert payload["provider"]["locale"]["models"]["qwen3.6-35b-a3b"]["tool_call"] is True


def test_the_file_never_reaches_a_commit_and_is_ignored_once(task, copy):
    permissions.write_profile(task, copy)
    permissions.write_profile(task, copy)

    ignore = git_in(copy, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    path = Path(ignore)
    if not path.is_absolute():
        path = copy / path
    text = path.read_text(encoding="utf-8")
    assert text.splitlines().count("/opencode.json") == 1
    assert git_in(copy, "status", "--porcelain").stdout.strip() == ""


def test_a_configuration_the_project_tracks_is_refused_not_overwritten(task, copy):
    (copy / permissions.CONFIG_FILE).write_text('{"model": "m"}\n', encoding="utf-8")
    git_in(copy, "add", permissions.CONFIG_FILE)
    git_in(copy, "commit", "-q", "-m", "config")

    with pytest.raises(permissions.PermissionProfileError, match="traccia"):
        permissions.write_profile(task, copy)
    assert json.loads((copy / permissions.CONFIG_FILE).read_text(encoding="utf-8")) == {"model": "m"}


def test_a_configuration_that_does_not_parse_is_left_alone(task, copy):
    (copy / permissions.CONFIG_FILE).write_text("{non json", encoding="utf-8")

    with pytest.raises(permissions.PermissionProfileError, match="non è JSON"):
        permissions.write_profile(task, copy)
    assert (copy / permissions.CONFIG_FILE).read_text(encoding="utf-8") == "{non json"


def test_a_folder_without_git_still_gets_the_profile(task, tmp_path):
    """Una copia che non è un repository non ha un diff da proteggere, ma il profilo serve."""
    folder = tmp_path / "sfusa"
    folder.mkdir()

    target = permissions.write_profile(task, folder)

    assert json.loads(target.read_text(encoding="utf-8"))["permissions"]


def test_a_git_error_in_a_real_copy_is_refused_not_swallowed(task, tmp_path, monkeypatch):
    """Con `.git` presente un fallimento di git non è «cartella senza git»."""
    folder = tmp_path / "copia"
    (folder / ".git").mkdir(parents=True)

    def failing(argv, **kwargs):
        return permissions.subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")

    monkeypatch.setattr(permissions.subprocess, "run", failing)

    with pytest.raises(permissions.PermissionProfileError, match="esclusione"):
        permissions.write_profile(task, folder)


def test_a_missing_copy_is_an_error(task, tmp_path):
    with pytest.raises(permissions.PermissionProfileError, match="inesistente"):
        permissions.write_profile(task, tmp_path / "non-c-e")


# --- le richieste che restano, e le risposte --------------------------------


class Harness:
    """Un `opencode api` finto: risponde JSON e ricorda cosa gli è stato chiesto."""

    def __init__(self, listing=None, reply=None, code=0, stdout=None, stderr=""):
        self.listing = {"data": []} if listing is None else listing
        self.reply = {"data": {}} if reply is None else reply
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        joined = " ".join(str(item) for item in argv)
        payload = self.listing if "session.permission.list" in joined else self.reply
        text = self.stdout if self.stdout is not None else json.dumps(payload)
        return permissions.subprocess.CompletedProcess(argv, self.code, text, self.stderr)

    def line(self, index=-1) -> str:
        return " ".join(str(item) for item in self.calls[index])


def _install(monkeypatch, harness: Harness) -> Harness:
    monkeypatch.setattr(permissions.subprocess, "run", harness)
    return harness


def test_pending_returns_the_requests_the_harness_lists(monkeypatch):
    harness = _install(monkeypatch, Harness(listing={"data": [{"id": "req_1"}, "scarto"]}))

    assert permissions.pending("ses_x") == [{"id": "req_1"}]
    assert "sessionID=ses_x" in harness.line()


def test_a_listing_that_is_not_a_list_is_an_error(monkeypatch):
    _install(monkeypatch, Harness(listing={"data": {"id": "req_1"}}))

    with pytest.raises(permissions.PermissionProfileError, match="non è una lista"):
        permissions.pending("ses_x")


def test_an_answer_names_the_session_the_request_and_the_decision(monkeypatch):
    harness = _install(monkeypatch, Harness())

    permissions.answer("ses_x", "req_1", "once")

    line = harness.line()
    assert "session.permission.reply" in line
    assert "sessionID=ses_x" in line
    assert "requestID=req_1" in line
    assert json.dumps({"decision": "once"}) in line


def test_a_decision_the_harness_does_not_know_is_refused_before_the_call(monkeypatch):
    harness = _install(monkeypatch, Harness())

    with pytest.raises(permissions.PermissionProfileError, match="decisione non valida"):
        permissions.answer("ses_x", "req_1", "sempre")
    assert harness.calls == []


def test_an_answer_without_a_request_is_refused(monkeypatch):
    _install(monkeypatch, Harness())

    with pytest.raises(permissions.PermissionProfileError, match="richiesta vuota"):
        permissions.answer("ses_x", "  ", "reject")


def test_a_call_that_fails_says_so_with_its_output(monkeypatch):
    _install(monkeypatch, Harness(code=1, stdout="", stderr="servizio spento"))

    with pytest.raises(permissions.PermissionProfileError, match="servizio spento"):
        permissions.pending("ses_x")


def test_an_answer_that_is_not_json_is_an_error(monkeypatch):
    _install(monkeypatch, Harness(stdout="<html>"))

    with pytest.raises(permissions.PermissionProfileError, match="non è JSON"):
        permissions.pending("ses_x")


def test_a_request_the_harness_does_not_know_is_an_error_not_a_silent_success(monkeypatch):
    _install(monkeypatch, Harness(reply={"_tag": "PermissionNotFoundError", "message": "assente"}))

    with pytest.raises(permissions.PermissionProfileError, match="PermissionNotFoundError"):
        permissions.answer("ses_x", "req_finta", "once")


def test_a_summary_is_bounded():
    text = permissions.summary({"id": "req_1", "testo": "x" * 500}, limit=60)

    assert len(text) <= 60
    assert text.endswith("…")


# --- lo stallo --------------------------------------------------------------


def _minutes_ago(minutes: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _attached(task_id, session="ses_x", unit="prova", at=""):
    """A unit running in a session, with the journal instant the test wants."""
    fields = {"unit": unit, "role": "esecutore", "harness": "opencode", "session": session}
    if at:
        fields["at"] = at
    state.append_event(task_id, "attached", **fields)


def _facts(updated_ms, idle_ms=None):
    return costs.SessionFacts(
        model="provider/modello", cost_usd=0.1, updated_ms=updated_ms, idle_ms=idle_ms,
    )


def test_the_default_stall_threshold_is_twenty_minutes(lane):
    assert policy.stall_minutes() == 20.0


def test_a_threshold_written_in_the_lane_is_used(lane):
    lane.mkdir(parents=True, exist_ok=True)
    (lane / policy.CONFIG_FILE).write_text(
        json.dumps({"supervision": {"stall_minutes": 5}}), encoding="utf-8",
    )

    assert policy.stall_minutes() == 5.0


@pytest.mark.parametrize("value", [0, -3, "venti", True])
def test_a_threshold_that_is_not_a_positive_number_is_an_error(lane, value):
    lane.mkdir(parents=True, exist_ok=True)
    (lane / policy.CONFIG_FILE).write_text(
        json.dumps({"supervision": {"stall_minutes": value}}), encoding="utf-8",
    )

    with pytest.raises(policy.PolicyError, match="stall_minutes"):
        policy.stall_minutes()


def test_a_session_that_stood_still_is_reported_and_journalled_once(task):
    _attached(task, at=_minutes_ago(60))
    now = time.time()
    old = int((now - 40 * 60) * 1000)

    found = stall.check(task, measure=lambda session: _facts(old), now=now)

    assert [item["unit"] for item in found] == ["prova"]
    assert found[0]["minutes"] == pytest.approx(40, abs=1)
    rows = [row for row in state.read_events(task) if row.get("event") == "unit_stalled"]
    assert len(rows) == 1
    assert rows[0]["measured_ms"] == old
    assert stall.check(task, measure=lambda session: _facts(old), now=now) == []
    assert stall.check(task, measure=lambda session: _facts(old), now=now + 7200) == []
    assert len([row for row in state.read_events(task) if row.get("event") == "unit_stalled"]) == 1


def test_a_stopped_session_is_finished_not_stalled(task):
    _attached(task, at=_minutes_ago(60))

    assert stall.check(task, measure=lambda session: _facts(1000, idle_ms=1000), now=time.time()) == []


def test_a_session_that_moved_recently_is_not_stalled(task):
    _attached(task, at=_minutes_ago(60))
    now = time.time()

    assert stall.check(task, measure=lambda session: _facts(int(now * 1000 - 60_000)), now=now) == []


def test_a_session_nobody_can_measure_is_not_accused(task):
    _attached(task, at=_minutes_ago(60))

    def broken(session):
        raise costs.CostError("export non leggibile")

    assert stall.check(task, measure=broken, now=time.time()) == []


def test_a_recent_journal_row_keeps_the_session_out_of_the_accusation(task):
    """Un export con un orologio che non torna non basta: il diario deve essere quieto."""
    _attached(task)
    now = time.time()

    assert stall.check(task, measure=lambda session: _facts(int((now - 40 * 60) * 1000)), now=now) == []


def test_the_threshold_of_the_lane_is_the_one_used(task):
    _attached(task, at=_minutes_ago(60))
    now = time.time()

    found = stall.check(task, measure=lambda session: _facts(int((now - 600) * 1000)), now=now, minutes=5)

    assert found and found[0]["threshold"] == 5.0


def test_the_journal_folds_the_stall_into_the_unit(task):
    _attached(task, at=_minutes_ago(60))
    now = time.time()

    stall.check(task, measure=lambda session: _facts(int((now - 40 * 60) * 1000)), now=now)

    record = state.unit_record(task, "prova")
    assert record["state"] == "in stallo"
    assert record["stalled_minutes"] == pytest.approx(40, abs=1)
    assert record["stall_threshold"] == 20.0


def test_a_session_that_moves_again_leaves_the_stall_behind(task):
    """L'etichetta non deve sopravvivere alla quiete che descriveva."""
    _attached(task, at=_minutes_ago(60))
    now = time.time()
    stall.check(task, measure=lambda session: _facts(int((now - 40 * 60) * 1000)), now=now)

    stall.check(task, measure=lambda session: _facts(int(now * 1000 - 30_000)), now=now)

    record = state.unit_record(task, "prova")
    assert record["state"] == "in corso"
    assert record["stalled_minutes"] == 0.0
    assert [row["event"] for row in state.read_events(task)][-1] == "unit_resumed"


def test_the_status_line_names_the_unit_and_the_threshold():
    line = stall.line({"unit": "prova", "minutes": 41.2, "threshold": 20.0})

    assert "prova" in line
    assert "41.2" in line
    assert "20" in line
    assert "permesso" in line


# --- i verbi ----------------------------------------------------------------


def test_status_shows_the_stall_and_points_at_the_permission_verb(task, monkeypatch, capsys):
    _attached(task, at=_minutes_ago(60))
    now = time.time()
    monkeypatch.setattr(costs, "measure", lambda session: _facts(int((now - 40 * 60) * 1000)))

    assert cli.main(["sup", "status", task]) == 0

    out = capsys.readouterr().out
    assert "in stallo da" in out
    assert f"sup permessi {task}" in out


def test_the_permission_verb_refuses_a_task_without_a_session(task, capsys):
    assert cli.main(["sup", "permessi", task]) == 1

    assert "nessuna sessione" in capsys.readouterr().err


def test_the_permission_verb_lists_what_the_session_waits_on(task, monkeypatch, capsys):
    _attached(task)
    harness = _install(monkeypatch, Harness(listing={"data": [
        {"id": "req_1", "title": "modifica fuori dalla copia"},
    ]}))

    assert cli.main(["sup", "permessi", task]) == 0

    out = capsys.readouterr().out
    assert "req_1" in out
    assert "1 richiesta" in out
    assert "--per-profilo" in out
    assert "sessionID=ses_x" in harness.line()


def test_an_answer_is_sent_and_journalled(task, monkeypatch, capsys):
    _attached(task)
    harness = _install(monkeypatch, Harness())

    assert cli.main(["sup", "permessi", task, "--allow", "req_1"]) == 0

    assert "req_1" in harness.line() and "requestID=req_1" in harness.line()
    assert "concessa una volta" in capsys.readouterr().out
    row = [row for row in state.read_events(task) if row.get("event") == "permission_answered"][-1]
    assert row["decision"] == "once"
    assert row["request"] == "req_1"
    assert row["unit"] == "prova"


def test_the_profile_closes_every_request_it_did_not_decide(task, monkeypatch, capsys):
    _attached(task)
    harness = _install(monkeypatch, Harness(listing={"data": [{"id": "req_1"}, {"id": "req_2"}]}))

    assert cli.main(["sup", "permessi", task, "--per-profilo"]) == 0

    assert harness.calls and all(
        "requestID=req_" in harness.line(index)
        for index in (1, 2)
    )
    decisions = [
        row for row in state.read_events(task) if row.get("event") == "permission_answered"
    ]
    assert [row["request"] for row in decisions] == ["req_1", "req_2"]
    assert all(row["decision"] == "reject" for row in decisions)
    assert all(row["reason"] == permissions.PROFILE_REFUSAL for row in decisions)


def test_an_answer_the_harness_refuses_writes_no_journal_row(task, monkeypatch, capsys):
    _attached(task)
    _install(monkeypatch, Harness(code=1, stderr="richiesta scaduta"))

    assert cli.main(["sup", "permessi", task, "--reject", "req_1"]) == 1

    assert "richiesta scaduta" in capsys.readouterr().err
    assert not [row for row in state.read_events(task) if row.get("event") == "permission_answered"]
