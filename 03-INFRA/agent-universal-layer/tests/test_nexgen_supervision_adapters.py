"""Gli adattatori degli harness: righe di comando vere, archivi finti, nessun processo reale.

Quello che si prova è il contratto: ogni harness costruisce l'argv che dichiara,
legge il proprio archivio, e dice «non lo so fare» invece di inventare una
sessione, un costo o un profilo. Claude Code e Codex sono provati sui loro file
(tmp), non sui processi installati sulla macchina.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import supervision_state as state
from nexgen_local import supervision_adapters as registry
from nexgen_local.supervision_adapters import antigravity, base, claude, codex, local, opencode
from nexgen_local.supervision_costs import SessionMissing


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """The lane's state directory, so no test reads the owner's own configuration."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def task(lane):
    return state.start("prova adattatori")["id"]


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


# --- registro e contratto ---------------------------------------------------


def test_the_registry_names_every_adapter_and_resolves_its_aliases():
    assert registry.names() == ("opencode", "claude", "codex", "agy", "local")
    assert registry.get("claude-code") is registry.get("claude")
    assert registry.get("antigravity") is registry.get("agy")
    assert registry.get("local-model") is registry.get("local")


def test_an_unknown_harness_is_refused_with_the_names_that_exist():
    with pytest.raises(base.AdapterError, match="harness sconosciuto") as caught:
        registry.get("inventato")
    assert "opencode" in str(caught.value) and "agy" in str(caught.value)
    assert registry.supports("inventato", "open_tab") is False


def test_the_capability_table_says_what_each_harness_can_and_cannot_do():
    lines = registry.capability_lines()
    assert len(lines) == len(registry.names())
    assert all("sa:" in line and "manca:" in line and "push:" in line for line in lines)
    assert registry.supports("agy", "scout") is True
    assert registry.supports("agy", "find_session") is False
    assert registry.supports("codex", "tokens") is True
    assert registry.supports("codex", "cost") is False


def test_an_operation_outside_the_contract_is_declared_absent_not_faked(tmp_path):
    class Nothing(base.Adapter):
        name = "niente"

    with pytest.raises(base.Unsupported, match="non sa open_tab") as caught:
        Nothing().tab_argv("binario", tmp_path, "prompt")
    assert caught.value.operation == "open_tab"
    assert "niente" in str(caught.value)


def test_a_folder_is_compared_apart_from_separators_case_and_windows_prefix():
    assert base.normal_path("C:\\Copia\\Unità") == base.normal_path("c:/copia/unità")
    assert base.normal_path("//?/C:/copia") == base.normal_path("C:/copia")
    assert base.normal_path("") == ""


# --- OpenCode ---------------------------------------------------------------


def test_opencode_builds_the_tab_turn_resume_and_scout_it_declares(tmp_path):
    adapter = opencode.OpenCodeAdapter()

    argv = adapter.tab_argv("oc", tmp_path, "leggi il brief", session="ses_1")
    assert argv == ["oc", str(tmp_path), "-s", "ses_1", "--prompt", "leggi il brief"]
    assert "--auto" not in argv
    assert adapter.tab_argv("oc", tmp_path, "p") == ["oc", str(tmp_path), "--prompt", "p"]

    assert adapter.send_argv("oc", "ses_1", "correggi", model="p/m") == [
        "oc", "run", "-s", "ses_1", "-m", "p/m", "correggi",
    ]
    assert adapter.scout_argv("oc", "guarda") == ["oc", "run", "--agent", "plan", "guarda"]
    assert adapter.resume_argv("oc", "ses_1", tmp_path, "riprendi") == [
        "oc", str(tmp_path), "-s", "ses_1", "--prompt", "riprendi",
    ]


def test_opencode_lists_sessions_with_its_own_command_from_the_copy(tmp_path):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen.update({"argv": list(argv), "cwd": kwargs.get("cwd")})
        return SimpleNamespace(returncode=0, stdout='[{"id": "ses_1"}]', stderr="")

    payload = opencode.OpenCodeAdapter().list_sessions(
        tmp_path, executable="oc-finto", runner=fake_run,
    )

    assert payload == [{"id": "ses_1"}]
    assert seen["cwd"] == str(tmp_path)
    assert seen["argv"][:5] == ["oc-finto", "session", "list", "--format", "json"]
    assert "100" in seen["argv"]


def test_opencode_refuses_a_session_list_it_cannot_read(tmp_path):
    def broken(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="non json", stderr="")

    with pytest.raises(base.AdapterError, match="non è JSON"):
        opencode.OpenCodeAdapter().list_sessions(tmp_path, executable="oc", runner=broken)

    def failed(argv, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="no")

    with pytest.raises(base.AdapterError, match="esce con 2"):
        opencode.OpenCodeAdapter().list_sessions(tmp_path, executable="oc", runner=failed)


def test_opencode_finds_the_newest_session_of_this_folder_only(tmp_path):
    adapter = opencode.OpenCodeAdapter()
    folder = tmp_path / "copia"
    sessions = [
        {"id": "vecchia", "directory": str(folder), "created": 1_000},
        {"id": "nuova", "directory": str(folder), "created": 9_000},
        {"id": "altrove", "directory": str(tmp_path / "altro"), "created": 10_000},
    ]

    found = adapter.find_session(
        folder, 9_000, wait_s=0, lister=lambda: sessions, clock=lambda: 0.0,
    )

    assert found is not None and found.id == "nuova"
    assert adapter.find_session(
        folder, 999_999, wait_s=0, lister=lambda: sessions, clock=lambda: 0.0,
    ) is None


def _api(answer):
    """One `opencode api` fake: `answer(argv)` decides what the server said."""
    def run(argv, **kwargs):
        return answer(list(argv))
    return run


def test_opencode_disposes_the_shared_servers_of_one_folder(tmp_path, monkeypatch):
    seen: dict = {}

    def answer(argv):
        if "/api/info" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"pid": 4242}), stderr="")
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(opencode.subprocess, "run", _api(answer))
    monkeypatch.setattr(opencode, "DISPOSE_WAIT_S", 0)
    monkeypatch.setattr(opencode.proces, "rows", lambda: [])

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is True
    assert str(tmp_path) in reason
    assert "debug.location.evict" in seen["argv"]
    assert f"x-opencode-directory:{tmp_path}" in seen["argv"]
    assert f"location[directory]={tmp_path}" in seen["argv"]


def test_opencode_says_when_the_eviction_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        opencode.subprocess, "run",
        lambda argv, **kwargs: SimpleNamespace(returncode=3, stdout="", stderr="busy"),
    )

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is False
    assert "esce con 3" in reason and "busy" in reason


def test_opencode_stops_the_children_the_service_kept_for_the_copy(tmp_path, monkeypatch):
    table = [
        {"pid": 4242, "ppid": 1, "name": "opencode.exe"},
        {"pid": 11, "ppid": 4242, "name": "python.exe"},
        {"pid": 12, "ppid": 4242, "name": "node.exe"},
    ]
    alive = {"yes": True}
    seen: dict = {}

    def answer(argv):
        if "/api/info" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"pid": 4242}), stderr="")
        if "/api/session/active" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def stop(pids):
        seen["killed"] = pids
        alive["yes"] = False

    monkeypatch.setattr(opencode, "DISPOSE_WAIT_S", 0)
    monkeypatch.setattr(opencode.subprocess, "run", _api(answer))
    monkeypatch.setattr(opencode.proces, "rows", lambda: list(table) if alive["yes"] else [])
    monkeypatch.setattr(opencode.proces, "cwd", lambda pid: tmp_path if pid in (11, 12) else None)
    monkeypatch.setattr(opencode.proces, "kill", stop)

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is True
    assert set(seen["killed"]) == {11, 12}
    assert "204" in reason and "fermati 2" in reason


def test_opencode_never_stops_the_children_of_an_active_session(tmp_path, monkeypatch):
    table = [
        {"pid": 4242, "ppid": 1, "name": "opencode.exe"},
        {"pid": 11, "ppid": 4242, "name": "python.exe"},
    ]

    def answer(argv):
        joined = " ".join(argv)
        if "/api/info" in joined:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"pid": 4242}), stderr="")
        if "/api/session/active" in joined:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"ses_x": {"type": "running"}}}), stderr="")
        if "/api/session/ses_x" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"location": {"directory": str(tmp_path)}}), stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def refuse(pids):
        raise AssertionError("nessun processo va fermato quando la sessione è attiva")

    monkeypatch.setattr(opencode, "DISPOSE_WAIT_S", 0)
    monkeypatch.setattr(opencode.subprocess, "run", _api(answer))
    monkeypatch.setattr(opencode.proces, "rows", lambda: list(table))
    monkeypatch.setattr(opencode.proces, "cwd", lambda pid: tmp_path if pid == 11 else None)
    monkeypatch.setattr(opencode.proces, "kill", refuse)

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is False
    assert "sessione attiva ses_x" in reason


def test_opencode_reads_the_folders_the_server_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(
        opencode.subprocess, "run",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps([{"directory": str(tmp_path)}]), stderr="",
        ),
    )

    assert opencode.OpenCodeAdapter().locations() == [str(tmp_path)]


# --- Claude Code ------------------------------------------------------------


def _claude_transcript(root: Path, session: str, rows: list[dict]) -> Path:
    folder = root / "projects" / "progetto"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session}.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8",
    )
    return path


def test_claude_names_the_session_itself_and_opens_without_bypassing_approvals(tmp_path):
    adapter = claude.ClaudeAdapter()
    first, second = adapter.new_session_id(), adapter.new_session_id()

    assert uuid.UUID(first) and uuid.UUID(second) and first != second
    argv = adapter.tab_argv("claude", tmp_path, "leggi il brief", session=first)
    assert argv == [
        "claude", "--session-id", first, "--permission-mode", "acceptEdits", "leggi il brief",
    ]
    assert "bypassPermissions" not in " ".join(argv)

    assert adapter.send_argv("claude", first, "correggi", model="m") == [
        "claude", "-p", "--resume", first, "--model", "m", "--permission-mode", "acceptEdits", "correggi",
    ]
    assert adapter.resume_argv("claude", first, tmp_path, "riprendi") == [
        "claude", "--resume", first, "riprendi",
    ]
    assert adapter.scout_argv("claude", "guarda") == [
        "claude", "-p", "--permission-mode", "plan", "guarda",
    ]


def test_claude_reads_cost_tokens_and_the_error_from_the_transcript(tmp_path, monkeypatch):
    session = str(uuid.UUID(int=1))
    root = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    _claude_transcript(root, session, [
        {"type": "assistant", "timestamp": "2026-10-21T10:00:00Z",
         "message": {"model": "claude-sonnet"}},
        {"type": "cost-state", "startTime": 1_790_000_000_000, "totalCostUSD": 0.25,
         "modelUsage": {"claude-sonnet": {
             "inputTokens": 100, "outputTokens": 20, "thinkingTokens": 5,
             "cacheReadInputTokens": 10, "cacheCreationInputTokens": 3,
         }}},
        {"type": "assistant", "isApiErrorMessage": True,
         "timestamp": "2026-10-21T10:01:00Z",
         "message": {"error": {"type": "overloaded_error", "message": "busy"}}},
    ])
    monkeypatch.setattr(claude.ClaudeAdapter, "running", lambda self, value: False)

    facts = claude.ClaudeAdapter().measure(session)

    assert facts.model == "claude-sonnet"
    assert facts.cost_usd == 0.25
    assert (facts.tokens_in, facts.tokens_out, facts.tokens_reasoning) == (100, 20, 5)
    assert (facts.tokens_cache_read, facts.tokens_cache_write) == (10, 3)
    assert facts.outcome == "failed"
    assert facts.error_type == "overloaded_error"
    assert facts.error_message == "busy"
    assert facts.stopped is True


def test_claude_says_when_a_transcript_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))

    with pytest.raises(SessionMissing, match="nessun transcript"):
        claude.ClaudeAdapter().measure("manca")


def test_claude_finds_the_transcript_of_the_id_the_engine_chose(tmp_path, monkeypatch):
    root = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))

    adapter = claude.ClaudeAdapter()
    with pytest.raises(base.AdapterError, match="senza l'identificativo"):
        adapter.find_session(tmp_path, 0, wait_s=0)
    assert adapter.find_session(tmp_path, 0, expected="assente", wait_s=0) is None

    session = str(uuid.UUID(int=2))
    _claude_transcript(root, session, [{"type": "assistant", "timestamp": "2026-10-21T10:00:00Z"}])
    found = adapter.find_session(tmp_path, 0, expected=session, wait_s=0)

    assert found is not None and found.id == session
    assert found.folder == str(tmp_path)


def test_claude_writes_a_project_profile_that_allows_edits_and_denies_push(task, tmp_path):
    folder = _git_repo(tmp_path / "copia")

    claude.ClaudeAdapter().write_profile(task, folder)

    payload = json.loads((folder / ".claude" / "settings.json").read_text(encoding="utf-8"))
    permissions = payload["permissions"]
    assert permissions["defaultMode"] == "acceptEdits"
    assert "Edit" in permissions["allow"] and "Write" in permissions["allow"]
    assert "Bash(git push *)" in permissions["deny"]
    assert "Bash(git -C * push *)" in permissions["deny"]
    assert str(state.open_task(task).resolve()) in permissions["additionalDirectories"]
    assert "bypassPermissions" not in json.dumps(permissions)
    exclude = subprocess.run(
        ["git", "-C", str(folder), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    exclude_path = Path(exclude) if Path(exclude).is_absolute() else folder / exclude
    assert "/.claude/settings.json" in exclude_path.read_text(encoding="utf-8")


# --- Codex ------------------------------------------------------------------


def _codex_store(root: Path, threads: list[dict], turns: list[dict]) -> None:
    """Two SQLite files shaped as the Codex CLI writes them, in a temporary CODEX_HOME."""
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "state_1.sqlite") as con:
        con.execute(
            "create table threads (id text, cwd text, model text, tokens_used integer, "
            "created_at integer, updated_at integer, title text)"
        )
        con.executemany(
            "insert into threads values (?, ?, ?, ?, ?, ?, ?)",
            [(row["id"], row["cwd"], row.get("model", ""), row.get("tokens"),
              row.get("created", 1_700_000_000), row.get("updated", 1_700_000_100), row.get("title", ""))
             for row in threads],
        )
    with sqlite3.connect(root / "thread_history_1.sqlite") as con:
        con.execute(
            "create table thread_turns (thread_id text, status text, error_json text, "
            "started_at integer, completed_at integer)"
        )
        con.executemany(
            "insert into thread_turns values (?, ?, ?, ?, ?)",
            [(row["thread_id"], row["status"], row.get("error_json"),
              row.get("started", 1_700_000_000), row.get("completed", 1_700_000_100))
             for row in turns],
        )


def test_codex_opens_the_sandbox_and_declares_how_the_push_is_stopped(tmp_path):
    adapter = codex.CodexAdapter()
    argv = adapter.tab_argv("codex", tmp_path, "leggi il brief")
    assert argv == [
        "codex", "-C", str(tmp_path), "-s", "workspace-write", "-a", "never",
        "--add-dir", str(tmp_path.parent), "leggi il brief",
    ]
    assert adapter.tab_argv("codex", tmp_path, "p", session="t1")[-3:] == ["resume", "t1", "p"]
    assert adapter.send_argv("codex", "t1", "correggi") == [
        "codex", "exec", "-s", "workspace-write", "resume", "t1", "correggi",
    ]
    assert adapter.scout_argv("codex", "guarda") == [
        "codex", "exec", "-s", "read-only", "guarda",
    ]
    assert "sandbox" in adapter.capabilities.deny_push
    assert "utente separato" in adapter.capabilities.limits
    assert "limite:" in adapter.capabilities.line()
    assert adapter.capabilities.has("cost") is False
    assert adapter.capabilities.has("tokens") is True


def test_codex_adds_the_task_folder_and_the_git_dir_as_writable_roots(tmp_path, monkeypatch):
    """La copia è un worktree: brief, resoconto e oggetti git stanno fuori dal suo workspace."""
    copy = tmp_path / "task" / "23-unita-prova"
    copy.mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git" / "worktrees" / "prova").mkdir(parents=True)
    (copy / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'prova'}\n", encoding="utf-8")
    home = tmp_path / "codex"
    _codex_store(home, [{"id": "t1", "cwd": str(copy)}], [])
    monkeypatch.setenv("CODEX_HOME", str(home))

    adapter = codex.CodexAdapter()
    tab = adapter.tab_argv("codex", copy, "p")
    assert tab == [
        "codex", "-C", str(copy), "-s", "workspace-write", "-a", "never",
        "--add-dir", str(copy.parent),
        "--add-dir", str(repo / ".git" / "worktrees" / "prova"),
        "--add-dir", str(repo / ".git"), "p",
    ]

    send = adapter.send_argv("codex", "t1", "correggi")
    roots = json.dumps([
        str(copy.parent),
        str(repo / ".git" / "worktrees" / "prova"),
        str(repo / ".git"),
    ])
    assert send == [
        "codex", "exec", "-s", "workspace-write",
        "-c", "sandbox_workspace_write.writable_roots=" + roots,
        "resume", "t1", "correggi",
    ]
    assert "-a" not in send


def test_codex_reads_threads_tokens_and_the_last_turn(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    folder = tmp_path / "copia"
    _codex_store(
        home,
        [{"id": "t1", "cwd": str(folder), "model": "gpt-5-codex", "tokens": 1234, "title": "prova"}],
        [{"thread_id": "t1", "status": "failed",
          "error_json": json.dumps({"type": "rate_limit", "message": "troppe richieste"})}],
    )
    adapter = codex.CodexAdapter()

    assert adapter.session_model("t1") == "gpt-5-codex"
    facts = adapter.measure("t1")
    assert facts.tokens_total == 1234
    assert facts.cost_usd is None
    assert "valgono i token" in facts.cost_note
    assert facts.outcome == "failed" and facts.error_type == "rate_limit"

    turn = adapter.last_turn("t1")
    assert turn.outcome == "failed" and turn.error_type == "rate_limit"
    assert turn.error_message == "troppe richieste"


def test_codex_finds_only_the_threads_of_this_folder_after_the_tab_opened(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    folder = tmp_path / "copia"
    _codex_store(
        home,
        [
            {"id": "giusto", "cwd": str(folder), "created": 1_700_000_000},
            {"id": "altro", "cwd": str(tmp_path / "altrove"), "created": 1_700_000_000},
            {"id": "vecchio", "cwd": str(folder), "created": 1_600_000_000},
        ],
        [],
    )

    found = codex.CodexAdapter().find_session(
        folder, 1_650_000_000_000, wait_s=0, clock=lambda: 0.0,
    )

    assert found is not None and found.id == "giusto"
    assert found.folder == str(folder)


def test_codex_says_when_the_store_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "vuoto"))

    with pytest.raises(base.AdapterError, match="archivio dei thread"):
        codex.CodexAdapter().threads()
    with pytest.raises(SessionMissing, match="archivio dei thread assente"):
        codex.CodexAdapter().measure("t1")


# --- agy (Antigravity) ------------------------------------------------------


def test_agy_is_a_scout_and_an_executor_that_cannot_be_followed(tmp_path):
    adapter = antigravity.AntigravityAdapter()
    assert adapter.tab_argv("agy", tmp_path, "leggi", session="c1") == [
        "agy", "--mode", "accept-edits", "--conversation", "c1", "--prompt-interactive", "leggi",
    ]
    assert adapter.scout_argv("agy", "guarda") == ["agy", "--mode", "plan", "--prompt", "guarda"]
    assert adapter.capabilities.has("find_session") is False
    assert adapter.capabilities.has("send_turn") is False
    assert adapter.capabilities.has("cost") is False
    assert "pushurl" in adapter.capabilities.deny_push

    with pytest.raises(base.Unsupported, match="non sa find_session"):
        adapter.find_session(tmp_path, 0)
    with pytest.raises(base.Unsupported, match="non sa send_turn"):
        adapter.send_argv("agy", "c1", "correggi")


# --- local model as an executor ---------------------------------------------


def test_local_is_opencode_with_the_model_the_lane_declares(tmp_path, monkeypatch):
    config = tmp_path / "lane" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"supervision": {"local_model": {"model": "ollama/qwen"}}}), encoding="utf-8",
    )
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    seen: dict = {}
    monkeypatch.setattr(
        opencode.OpenCodeAdapter, "write_profile",
        lambda self, task_id, folder, model="", models=None: seen.update(
            {"task": task_id, "folder": folder, "model": model, "models": models}
        ),
    )

    adapter = local.LocalAdapter()
    assert adapter.model() == "ollama/qwen"
    adapter.write_profile("20261022-000000-prova", tmp_path)
    assert seen["model"] == "ollama/qwen"
    assert seen["models"] == {"ollama": {"qwen": {"name": "qwen (locale)", "tool_call": True}}}
    assert adapter.capabilities.has("find_session") is True


def test_a_local_unit_without_a_model_in_the_lane_is_refused(lane, tmp_path):
    adapter = local.LocalAdapter()
    with pytest.raises(base.AdapterError, match="nessun modello locale configurato"):
        adapter.write_profile("20261022-000000-prova", tmp_path)
