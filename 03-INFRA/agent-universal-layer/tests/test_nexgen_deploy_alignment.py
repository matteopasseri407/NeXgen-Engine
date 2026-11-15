"""L'allineamento del server: le tre identità e la riga del doctor.

Nessun test qui parla con la rete né avvia Docker: la rotta di salute è una
funzione sostituita, e il resto sono file in una cartella temporanea. Ciò che va
garantito è la regola, non la sua messa in scena: un server che gira su un
commit diverso da quello del motore è un guasto, un server che non risponde è
una misura che manca, e una macchina senza server non produce nessuna riga.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks import deploy_checks  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.deploy import alignment  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english_messages():
    """Pins the language: these tests compare message text, not translations."""
    from nexgen_core import i18n

    i18n.set_language("en")
    try:
        yield
    finally:
        i18n.set_language(None)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _checkout(tmp_path: Path) -> Path:
    """A real git checkout, so `engine_commit` reads a real HEAD."""
    repo = tmp_path / "engine"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "VERSION").write_text("0.5.0\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")[:9]


def _vault(tmp_path: Path, *, pin: str | None = None, mode: str | None = None) -> Path:
    vault = tmp_path / "vault"
    (vault / "99-INDEX").mkdir(parents=True, exist_ok=True)
    if pin is not None:
        (vault / "99-INDEX" / "ENGINE-PIN.txt").write_text(f"{pin}\n", encoding="utf-8")
    if mode is not None:
        (vault / "99-INDEX" / "USER-PROFILE.md").write_text(
            f"## Architecture\n\n- **Mode**: `{mode}`\n", encoding="utf-8"
        )
    return vault


def test_the_short_commit_is_nine_lowercase_characters():
    assert alignment.short_commit("  ABCDEF1234567890 \n") == "abcdef123"
    assert alignment.short_commit("") is None
    assert alignment.short_commit(None) is None


def test_engine_commit_reads_the_checkout_head(tmp_path: Path):
    repo = _checkout(tmp_path)
    assert alignment.engine_commit(repo) == _head(repo)


def test_engine_commit_is_none_outside_a_checkout(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert alignment.engine_commit(plain) is None


def test_the_pin_is_read_from_the_vault(tmp_path: Path):
    assert alignment.pinned_commit(_vault(tmp_path, pin="ABCDEF123456")) == "abcdef123"
    assert alignment.pinned_commit(_vault(tmp_path / "other")) is None


def test_an_unedited_profile_template_is_not_a_cloud_server(tmp_path: Path):
    vault = _vault(tmp_path, mode="[LOCAL-ONLY] or [CLOUD-SERVER]")
    assert alignment.cloud_server_install(vault) is False


def test_a_declared_cloud_server_is_recognised(tmp_path: Path):
    assert alignment.cloud_server_install(_vault(tmp_path, mode="[CLOUD-SERVER]")) is True


def test_the_health_route_keeps_the_origin_of_the_mcp_url():
    assert alignment.health_url("http://127.0.0.1:8081/mcp") == "http://127.0.0.1:8081/healthz"
    assert alignment.health_url("http://localhost:9123/some/path") == "http://localhost:9123/healthz"


def test_the_mcp_url_comes_from_the_variable_the_manifest_uses(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VAULT_LIBRARY_URL", "http://127.0.0.1:9999/mcp")
    assert alignment.mcp_url() == "http://127.0.0.1:9999/mcp"
    assert alignment.mcp_url({}) == alignment.DEFAULT_MCP_URL


def _as_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_core import roles

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.SERVER_ROLE)


def _as_workstation(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_core import roles

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.STANDALONE_ROLE)


def _outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, state: alignment.Alignment):
    """Run the check with the reading replaced, so each case is one literal."""
    monkeypatch.setattr(alignment, "measure", lambda **_: state)
    return deploy_checks.check_deploy_alignment(tmp_path / "home", _vault(tmp_path), tmp_path / "engine")


def test_a_machine_without_a_server_produces_no_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _as_workstation(monkeypatch)
    state = alignment.Alignment(engine="abc123456", pin=None, deployed="abc123456", answered=True)
    assert _outcome(tmp_path, monkeypatch, state=state) is None


def test_the_cloud_server_workstation_asks_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _as_workstation(monkeypatch)
    state = alignment.Alignment(engine="abc123456", pin=None, deployed="abc123456", answered=True)
    monkeypatch.setattr(alignment, "measure", lambda **_: state)
    vault = _vault(tmp_path / "v", mode="[CLOUD-SERVER]")

    outcome = deploy_checks.check_deploy_alignment(tmp_path / "home", vault, tmp_path / "engine")

    assert outcome is not None
    assert outcome.severity == Severity.OK


def test_engine_pin_and_deployed_server_on_one_commit_is_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _as_server(monkeypatch)
    state = alignment.Alignment(engine="abc123456", pin="abc123456", deployed="abc123456", answered=True)

    outcome = _outcome(tmp_path, monkeypatch, state=state)

    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert outcome.id == "deploy.alignment"


def test_a_server_built_from_another_commit_is_a_failure_with_one_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _as_server(monkeypatch)
    state = alignment.Alignment(engine="abc123456", pin="abc123456", deployed="def789012", answered=True)

    outcome = _outcome(tmp_path, monkeypatch, state=state)

    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "def789012" in outcome.message and "abc123456" in outcome.message
    assert "nexgen deploy vault-mcp" in (outcome.action or "")


def test_a_server_that_does_not_say_its_commit_is_a_warning_with_the_same_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An image older than the commit stamp is what every server has after updating from 2.29:
    as a failure it made the final doctor of that update fail, and the update rolled back."""
    _as_server(monkeypatch)
    state = alignment.Alignment(engine="abc123456", pin=None, deployed=None, answered=True)

    outcome = _outcome(tmp_path, monkeypatch, state=state)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "nexgen deploy vault-mcp" in (outcome.action or "")


def test_a_server_that_did_not_answer_is_undetermined_not_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _as_server(monkeypatch)
    state = alignment.Alignment(
        engine="abc123456", pin=None, deployed=None, answered=False,
        detail="http://127.0.0.1:8081/healthz could not be reached",
    )

    outcome = _outcome(tmp_path, monkeypatch, state=state)

    assert outcome is not None
    assert outcome.severity == Severity.UNDETERMINED
    assert "could not be reached" in outcome.message


def test_a_pin_behind_this_engine_is_only_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _as_server(monkeypatch)
    state = alignment.Alignment(engine="abc123456", pin="1112" "22333", deployed="abc123456", answered=True)

    outcome = _outcome(tmp_path, monkeypatch, state=state)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "1112" "22333" in outcome.message


def test_measure_joins_the_checkout_the_pin_and_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _checkout(tmp_path)
    vault = _vault(tmp_path, pin=_head(repo))
    monkeypatch.setattr(alignment, "get_json", lambda url, timeout: ({"status": "ok", "engine_commit": _head(repo)}, ""))

    state = alignment.measure(engine_root=repo, vault_data=vault, mcp_address="http://127.0.0.1:8081/mcp")

    assert state.engine == _head(repo)
    assert state.pin == _head(repo)
    assert state.deployed == _head(repo)
    assert state.aligned is True


def test_measure_says_which_route_it_could_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _checkout(tmp_path)
    monkeypatch.setattr(alignment, "get_json", lambda url, timeout: (None, "nothing listening"))

    state = alignment.measure(engine_root=repo, vault_data=_vault(tmp_path), mcp_address="http://127.0.0.1:8081/mcp")

    assert state.answered is False
    assert state.deployed is None
    assert state.detail == "nothing listening"


def test_a_server_built_before_changes_that_never_touched_it_is_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every update moves the engine commit; most do not touch the server, and calling those
    servers 'behind' made the final doctor roll the update back on the VPS."""
    _as_server(monkeypatch)
    state = alignment.Alignment(engine="aeba8c527", pin="aeba8c527", deployed="f8c70d175", answered=True,
                                service_unchanged=True)

    outcome = _outcome(tmp_path, monkeypatch, state=state)

    assert outcome.severity == Severity.OK
    assert "f8c70d175" in outcome.message


def test_a_server_whose_own_files_changed_since_its_build_is_still_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _as_server(monkeypatch)
    state = alignment.Alignment(engine="aeba8c527", pin="aeba8c527", deployed="f8c70d175", answered=True,
                                service_unchanged=False)

    outcome = _outcome(tmp_path, monkeypatch, state=state)

    assert outcome.severity == Severity.BROKEN


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_the_service_question_is_asked_of_the_service_folder_only(tmp_path: Path):
    repo = tmp_path / "engine"
    (repo / "deploy" / "vault-mcp").mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "deploy" / "vault-mcp" / "server.py").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "server v1")
    built = _git(repo, "rev-parse", "HEAD")
    (repo / "other.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "engine only")
    unrelated = _git(repo, "rev-parse", "HEAD")
    (repo / "deploy" / "vault-mcp" / "server.py").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "server v2")
    changed = _git(repo, "rev-parse", "HEAD")

    assert alignment._service_unchanged(repo, built[:9], unrelated[:9]) is True
    assert alignment._service_unchanged(repo, built[:9], changed[:9]) is False
    assert alignment._service_unchanged(repo, "0123abcde", changed[:9]) is None
