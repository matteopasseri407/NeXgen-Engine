"""Il vault-mcp spedito nel deploy: promesse verificabili, non raccontate.

Tre regole:
1. il tag dell'immagine nel compose segue `__version__` (il commento del
   compose lo promette da due release; il test non esisteva, ora esiste);
2. l'indice di lettura esclude 99-SECRETS per default, come il write path:
   search/read su un albero segreti è esattamente la via di recupero che le
   regole del vault vietano;
3. gli snippet di search sono orientamento, non fedeltà: tutto ciò che ha
   forma di segreto esce mascherato.

E una quarta, sul percorso che vede il modello: ogni tool che prende una nota
accetta gli stessi nomi (`note_ref` e i suoi alias) e un rifiuto arriva al
modello con il testo che gli dice cosa mandare, non come "Error executing tool".
"""
from __future__ import annotations

import hashlib
import os
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import yaml

VAULT_MCP = Path(__file__).resolve().parents[2] / "deploy" / "vault-mcp"
SRC = VAULT_MCP / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vault_mcp_server import __version__ as vault_mcp_version  # noqa: E402
from vault_mcp_server.config import Settings  # noqa: E402
from vault_mcp_server.vault import SNIPPET_SIZE, VaultService, _make_snippet, _redact_snippet  # noqa: E402


def _settings(tmp_path: Path, **env: str) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    saved = {k: os.environ.pop(k, None) for k in ("VAULT_ROOT", "EXCLUDE_PATH_PREFIXES", *env.keys())}
    os.environ["VAULT_ROOT"] = str(vault)
    os.environ.update(env)
    try:
        return Settings.from_env()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_the_compose_image_tag_tracks_the_server_version():
    compose = yaml.safe_load((VAULT_MCP / "docker-compose.yml").read_text(encoding="utf-8"))
    image = compose["services"]["vault-mcp"]["image"]
    # il compose scrive ${VAULT_MCP_IMAGE:-vault-mcp:X.Y.Z}: il default
    # resta tale finché qualcuno non interpola la variabile
    default_tag = image.split(":-")[-1].rstrip("}")
    assert default_tag == f"vault-mcp:{vault_mcp_version}", (
        "il default del tag immagine deve seguire __version__, altrimenti "
        "ogni build sovrascrive lo stesso tag e non esiste più rollback"
    )


def test_the_service_runs_an_init_process_that_reaps_orphans():
    compose = yaml.safe_load((VAULT_MCP / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["vault-mcp"]
    # Il server scrive sul vault con git, e git stacca in background la
    # manutenzione automatica (`gc.autoDetach` vale true di default). Quei
    # nipoti restano orfani: se il processo del server e' PID 1, nessuno li
    # raccoglie e diventano zombie. `init: true` mette un init davanti al
    # server, ed e' lui a raccoglierli.
    assert service.get("init") is True, (
        "il servizio deve girare con init: true, altrimenti i processi git "
        "staccati restano zombie nel contenitore (misurati 1122 il 2026-10-25)"
    )


@pytest.mark.skipif(os.name == "nt", reason="symlinks require privileges")
def test_note_writer_never_follows_a_stale_predictable_temp_symlink(tmp_path):
    settings = _settings(tmp_path)
    target = settings.vault_root / "note.md"
    target.write_text("old")
    outside = tmp_path / "outside.md"
    outside.write_text("untouched")
    stale = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    stale.symlink_to(outside)
    VaultService(settings)._write_note_file(target, "new", already_validated=True)
    assert outside.read_text() == "untouched"
    assert target.read_text() == "new"
    assert stale.is_symlink()


def test_note_writer_cleans_only_its_temp_after_failed_rename(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    target = settings.vault_root / "note.md"
    target.write_text("old")
    other = settings.vault_root / ".another-writer.tmp"
    other.write_text("untouched")

    def fail(*args):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        VaultService(settings)._write_note_file(target, "new", already_validated=True)
    assert target.read_text() == "old"
    assert other.read_text() == "untouched"
    assert set(settings.vault_root.iterdir()) == {target, other}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_new_note_is_private_during_publication(tmp_path):
    import stat
    settings = _settings(tmp_path)
    target = settings.vault_root / "note.md"
    VaultService(settings)._write_note_file(target, "new", already_validated=True)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_note_replacement_preserves_existing_reader_permissions(tmp_path):
    import stat
    settings = _settings(tmp_path)
    target = settings.vault_root / "note.md"
    target.write_text("old")
    target.chmod(0o640)
    VaultService(settings)._write_note_file(target, "new", already_validated=True)
    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_the_read_index_excludes_99_secrets_by_default(tmp_path: Path):
    settings = _settings(tmp_path)
    assert settings.exclude_path_prefixes == ("99-SECRETS",)


def test_an_explicit_exclude_env_still_wins(tmp_path: Path):
    settings = _settings(tmp_path, EXCLUDE_PATH_PREFIXES="99-SECRETS,privato")
    assert settings.exclude_path_prefixes == ("99-SECRETS", "privato")


def test_secrets_never_reach_a_search_snippet(tmp_path: Path):
    settings = _settings(tmp_path)
    note = settings.vault_root / "note.md"
    note.write_text(
        "# Nota\n\n"
        "API_KEY = sk-live-abcdefghij0123456789\n"
        "AGE-SECRET-KEY-QQJKLMNOPQRSTUVWXYZ012345\n"
        "La nota parla anche di roba innocua.\n",
        encoding="utf-8",
    )
    vault = VaultService(settings)

    result = vault.search_notes(query="API_KEY", limit=5)

    assert result["matches"], "la nota deve restare trovabile"
    snippet = result["matches"][0]["snippet"]
    assert "sk-live" not in snippet
    assert "AGE-SECRET-KEY" not in snippet
    assert "[redacted]" in snippet


def test_snippets_are_short_enough_to_be_orientation(tmp_path: Path):
    body = "parola " * 400
    snippet = _make_snippet(body, "parola", ["parola"])
    assert len(snippet) <= SNIPPET_SIZE + 60  # finestra più le ellissi, non un numero magico


@pytest.mark.parametrize(
    "secret",
    [
        # le forme sono assemblate a runtime: il leak-scan del repo blocca
        # le forme di segreto scritte letteralmente nel sorgente, e un test
        # deve verificare il redattore senza sembrare una fuga
        "bearer " + "eyJhbGciOiJIUzI1NiJ9.e30.abc",
        "-----BEGIN " + "PRIVATE" + " KEY-----\nMIIabc\n-----END " + "PRIVATE" + " KEY-----",
        "password: hunter2-secret-value",
        "0123456789abcdef" * 3,
    ],
)
def test_the_redactor_masks_the_secret_shapes(secret):
    assert _redact_snippet(secret) == "[redacted]"
    assert _redact_snippet(f"testo normale con {secret} dentro") == "testo normale con [redacted] dentro"


def test_ordinary_prose_is_never_masked():
    text = "Questa frase parla di architettura e di vault, senza nulla da nascondere."
    assert _redact_snippet(text) == text


# --- Hardening: write needs a token, a wrong token is a 401, a crashed write does not wedge the vault ---


def _git(repo: Path, *args: str) -> None:
    import subprocess
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8")


def _write_settings(tmp_path: Path, **env: str) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    if not (vault / ".git").exists():
        _git(vault, "init", "-q")
        _git(vault, "config", "user.email", "t@example.com")
        _git(vault, "config", "user.name", "t")
        (vault / "seed.md").write_text("# seed\n", encoding="utf-8")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "seed")
    return _settings(tmp_path, VAULT_WRITE_ENABLED="true", VAULT_GIT_DIR=str(vault / ".git"), **env)


def test_a_write_enabled_server_refuses_to_start_without_a_token(tmp_path):
    with pytest.raises(ValueError, match="VAULT_TOKEN"):
        _write_settings(tmp_path)
    assert _write_settings(tmp_path, VAULT_TOKEN="s3cret").vault_token == "s3cret"


def test_a_read_only_server_may_still_run_without_a_token(tmp_path):
    assert _settings(tmp_path).vault_token is None


def _call(settings, header: bytes) -> int:
    import asyncio

    from vault_mcp_server.server import McpSecurityMiddleware

    reached = []

    async def inner(scope, receive, send):
        reached.append(True)

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    scope = {"type": "http", "path": "/mcp", "headers": [(b"authorization", header)], "method": "POST"}
    asyncio.run(McpSecurityMiddleware(inner, settings)(scope, receive, send))
    if reached:
        return 200
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_non_ascii_bearer_is_a_401_not_a_crash(tmp_path):
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret")
    assert _call(settings, "Bearer té".encode("latin-1")) == 401
    assert _call(settings, b"Bearer wrong") == 401
    assert _call(settings, b"Bearer s3cret") == 200


def test_an_orphan_temp_file_does_not_block_every_later_write(tmp_path):
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret")
    vault = VaultService(settings)
    (settings.vault_root / ".seed.md.k3j2h1.tmp").write_text("left by a crash", encoding="utf-8")
    result = vault.create_note("fresh.md", "# fresh\n")
    assert result["committed"] is True


def test_a_real_uncommitted_change_still_blocks_a_write(tmp_path):
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret")
    vault = VaultService(settings)
    (settings.vault_root / "seed.md").write_text("# edited by hand\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="uncommitted"):
        vault.create_note("fresh.md", "# fresh\n")
    (settings.vault_root / "seed.md").write_text("# seed\n", encoding="utf-8")
    (settings.vault_root / "untracked note.md").write_text("mine\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="uncommitted"):
        vault.create_note("fresh.md", "# fresh\n")


#: `CRLF_NOTE` is the same note as it sits on a Windows checkout. The expected
#: hash below is taken over `LF_NOTE`, the note's canonical form, as a literal.
#: The note carries frontmatter so the expected hash also proves the section
#: offsets survive the frontmatter match on both line endings.
LF_NOTE = b"---\ntitle: crlf\n---\n\n# Crlf\n\n## Keep\n\nkeep body\n\n## Edit\n\nold body\n"
CRLF_NOTE = LF_NOTE.replace(b"\n", b"\r\n")


def _crlf_vault(tmp_path: Path) -> Settings:
    """A write-enabled vault with one committed note stored as CRLF bytes.

    A Windows checkout keeps a note exactly as written, and the read index
    hashes those bytes, while the compare-and-swap used to read the same file
    through Python's newline translation. Writing the CRLF bytes by hand makes
    the case real on Linux too.
    """
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret")
    (settings.vault_root / "crlf.md").write_bytes(CRLF_NOTE)
    _git(settings.vault_root, "add", "-A")
    _git(settings.vault_root, "commit", "-q", "-m", "crlf note")
    return settings


def test_a_crlf_note_read_then_updated_is_not_stale(tmp_path: Path):
    settings = _crlf_vault(tmp_path)
    vault = VaultService(settings)

    note = vault.read_note("crlf.md")
    assert note["content_hash"] == hashlib.sha256(LF_NOTE).hexdigest()
    with pytest.raises(ValueError, match="expected_hash"):
        vault.update_note("crlf.md", "# Crlf\n\nagain\n", expected_hash="0" * 64)

    result = vault.update_note("crlf.md", "# Crlf\n\nrewritten\n", expected_hash=note["content_hash"])

    assert result["action"] == "updated"
    assert result["old_content_hash"] == note["content_hash"]
    assert result["committed"] is True
    stored = (settings.vault_root / "crlf.md").read_text(encoding="utf-8")
    assert stored.replace("\r\n", "\n") == "# Crlf\n\nrewritten\n"


def test_a_crlf_note_read_then_one_section_updated_is_not_stale(tmp_path: Path):
    settings = _crlf_vault(tmp_path)
    vault = VaultService(settings)

    section_hashes = {s["heading"]: s["content_hash"] for s in vault.read_note("crlf.md")["sections"]}
    result = vault.update_section(
        "crlf.md", "## Edit", "## Edit\n\nnew body\n", expected_hash=section_hashes["## Edit"]
    )

    assert result["action"] == "updated_section"
    assert result["old_section_hash"] == section_hashes["## Edit"]
    stored = (settings.vault_root / "crlf.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert stored == "---\ntitle: crlf\n---\n\n# Crlf\n\n## Keep\n\nkeep body\n\n## Edit\n\nnew body\n"


def test_read_note_accepts_each_alias():
    from vault_mcp_server.note_ref import ALIASES, resolve_note_ref

    resolved = [resolve_note_ref(**{name: "sample.md"}) for name in ALIASES]

    assert ALIASES[0] == "note_ref"
    assert resolved == ["sample.md"] * 4


def test_read_note_fails_when_none_given():
    from vault_mcp_server.note_ref import resolve_note_ref

    with pytest.raises(ValueError, match="note_ref"):
        resolve_note_ref(note_ref=None, path="  ")


def test_read_note_fails_when_two_different_given():
    from vault_mcp_server.note_ref import resolve_note_ref

    with pytest.raises(ValueError, match="note_ref"):
        resolve_note_ref(note_ref="sample.md", path="other.md")


def test_read_note_takes_the_same_value_under_two_names():
    from vault_mcp_server.note_ref import resolve_note_ref

    assert resolve_note_ref(note_ref="sample.md", path=" sample.md ") == "sample.md"
    assert resolve_note_ref(relative_path="dir/sample.md", note_path="") == "dir/sample.md"


# --- Il percorso del modello: create_server() e call_tool, non solo la funzione pura ---

#: I nomi che il modello manda davvero; il primo è quello canonico.
NOTE_NAME_ALIASES = ("note_ref", "path", "note_path", "relative_path")


def _mcp_call(settings: Settings, name: str, arguments: dict[str, Any]) -> Any:
    """Call one tool on the real server, as a model does, and return its result."""
    import asyncio

    from vault_mcp_server.server import create_server

    mcp, _vault = create_server(settings)
    return asyncio.run(mcp.call_tool(name, arguments))


def _single_line_vault(tmp_path: Path) -> Settings:
    """A write-enabled vault whose seed note is one line with no newline at all.

    The smallest note the compare-and-swap can be exercised on, so the alias
    tests below stay about the reference name. The line endings that used to
    break the round trip have their own tests
    (`test_a_crlf_note_read_then_updated_is_not_stale` and its section sibling).
    """
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret")
    (settings.vault_root / "seed.md").write_text("# seed", encoding="utf-8")
    _git(settings.vault_root, "add", "-A")
    _git(settings.vault_root, "commit", "-q", "-m", "single line seed")
    return settings


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_read_note_over_mcp_takes_the_note_under_every_name(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.vault_root / "sample.md").write_text("# Sample\n", encoding="utf-8")

    for name in NOTE_NAME_ALIASES:
        result = _mcp_call(settings, "read_note", {name: "sample.md"})
        assert result.structured_content["path"] == "sample.md", name
        assert result.structured_content["title"] == "Sample", name


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_list_related_over_mcp_takes_the_note_under_every_name(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.vault_root / "hub.md").write_text("# Hub\n\nSee [[leaf]].\n", encoding="utf-8")
    (settings.vault_root / "leaf.md").write_text("# Leaf\n", encoding="utf-8")

    for name in NOTE_NAME_ALIASES:
        result = _mcp_call(settings, "list_related", {name: "hub.md"})
        assert result.structured_content["note"] == "hub.md", name
        assert [(item["path"], item["relationship"]) for item in result.structured_content["related"]] == [
            ("leaf.md", "outgoing_link")
        ], name


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_append_note_over_mcp_takes_the_note_under_every_name(tmp_path: Path):
    """Kurisu sent `append_note` the note under `note_ref` and was refused: every name must reach the same note."""
    settings = _single_line_vault(tmp_path)

    for name in NOTE_NAME_ALIASES:
        result = _mcp_call(settings, "append_note", {name: "seed.md", "content": f"line from {name}\n"})
        assert result.structured_content["committed"] is True, name

    text = (settings.vault_root / "seed.md").read_text(encoding="utf-8")
    assert all(f"line from {name}" in text for name in NOTE_NAME_ALIASES)


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_create_note_over_mcp_takes_the_path_under_every_name(tmp_path: Path):
    settings = _single_line_vault(tmp_path)

    for name in NOTE_NAME_ALIASES:
        result = _mcp_call(settings, "create_note", {name: f"new-{name}.md", "content": "# new\n"})
        assert result.structured_content["committed"] is True, name
        assert (settings.vault_root / f"new-{name}.md").is_file(), name


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_update_note_over_mcp_takes_the_note_under_an_alias(tmp_path: Path):
    settings = _single_line_vault(tmp_path)
    note = _mcp_call(settings, "read_note", {"note_ref": "seed.md"}).structured_content

    result = _mcp_call(
        settings,
        "update_note",
        {"path": "seed.md", "content": "# replaced\n", "expected_hash": note["content_hash"]},
    )

    assert result.structured_content["action"] == "updated"
    assert result.structured_content["committed"] is True
    assert (settings.vault_root / "seed.md").read_text(encoding="utf-8").replace("\r\n", "\n") == "# replaced\n"


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_update_section_over_mcp_takes_the_note_under_an_alias(tmp_path: Path):
    settings = _single_line_vault(tmp_path)
    note = _mcp_call(settings, "read_note", {"note_ref": "seed.md"}).structured_content

    result = _mcp_call(
        settings,
        "update_section",
        {
            "note_path": "seed.md",
            "section_heading": "# seed",
            "content": "# seed\n\nbody\n",
            "expected_hash": note["sections"][0]["content_hash"],
        },
    )

    assert result.structured_content["action"] == "updated_section"
    assert result.structured_content["committed"] is True
    assert (settings.vault_root / "seed.md").read_text(encoding="utf-8").replace("\r\n", "\n") == "# seed\n\nbody\n"


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_missing_note_reference_reaches_the_model(tmp_path: Path):
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

    settings = _settings(tmp_path)
    with pytest.raises(ToolError) as raised:
        _mcp_call(settings, "read_note", {})

    # the SDK answers any other exception with "Error executing tool read_note"
    # alone, so the diagnostic the model reads proves it travelled as a ToolError
    assert not isinstance(raised.value, UnexpectedToolError)
    assert "note_ref" in str(raised.value)
    assert "path" in str(raised.value)


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_conflicting_note_references_reach_the_model(tmp_path: Path):
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

    settings = _settings(tmp_path)
    (settings.vault_root / "sample.md").write_text("# Sample\n", encoding="utf-8")
    with pytest.raises(ToolError) as raised:
        _mcp_call(settings, "read_note", {"note_ref": "sample.md", "path": "other.md"})

    assert not isinstance(raised.value, UnexpectedToolError)
    assert "conflicting" in str(raised.value)
    assert "other.md" in str(raised.value)


# --- Ogni errore di dominio dice al modello il motivo e il passo dopo ---
#
# "Error executing tool read_note" da solo è la forma che ha prodotto 9
# chiamate fallite e 0 retry utili in 14 giorni: il modello non sa se il nome
# era sbagliato, se qualcun altro ha scritto la nota, o se il percorso è
# vietato, quindi ritenta alla cieca. Qui si verifica il testo che legge.


def _tool_error(settings: Settings, name: str, arguments: dict[str, Any]) -> str:
    """The message a model reads for a call that must fail, proven to be a `ToolError`."""
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

    with pytest.raises(ToolError) as raised:
        _mcp_call(settings, name, arguments)

    assert not isinstance(raised.value, UnexpectedToolError), "the reason did not travel as a ToolError"
    return str(raised.value)


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_missing_note_names_the_closest_ones_and_the_next_tool(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.vault_root / "deploy-runbook.md").write_text("# Deploy runbook\n", encoding="utf-8")

    message = _tool_error(settings, "read_note", {"note_ref": "deploy-runbok.md"})

    assert "No note matches 'deploy-runbok.md'" in message
    assert "deploy-runbook.md" in message
    assert "search_notes" in message


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_an_ambiguous_reference_says_which_paths_it_means(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.vault_root / "one").mkdir()
    (settings.vault_root / "two").mkdir()
    (settings.vault_root / "one" / "notes.md").write_text("# One\n", encoding="utf-8")
    (settings.vault_root / "two" / "notes.md").write_text("# Two\n", encoding="utf-8")

    message = _tool_error(settings, "read_note", {"note_ref": "notes"})

    assert "Ambiguous note reference 'notes'" in message
    assert "one/notes.md" in message and "two/notes.md" in message
    assert "full relative path" in message


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_hash_mismatch_says_how_to_get_the_right_one(tmp_path: Path):
    settings = _single_line_vault(tmp_path)

    message = _tool_error(
        settings, "update_note", {"note_ref": "seed.md", "content": "# replaced\n", "expected_hash": "0" * 64}
    )

    assert "expected_hash does not match" in message
    assert "read_note" in message
    assert "content_hash" in message


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_an_unknown_section_names_the_headings_it_can_address(tmp_path: Path):
    settings = _crlf_vault(tmp_path)

    message = _tool_error(
        settings,
        "update_section",
        {"note_ref": "crlf.md", "section_heading": "## Missing", "content": "## Missing\n\nx\n", "expected_hash": "0" * 64},
    )

    assert "Section not found: '## Missing'" in message
    assert "## Keep" in message and "## Edit" in message
    assert "section_heading" in message


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_path_the_write_rule_refuses_says_which_rule(tmp_path: Path):
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret")

    message = _tool_error(settings, "create_note", {"note_path": "99-SECRETS/leak.md", "content": "# leak\n"})

    assert "blocked: 99-SECRETS/leak.md" in message
    assert "99-SECRETS" in message
    assert "Nothing was written" in message


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_write_over_the_size_limit_says_how_much_and_what_to_do(tmp_path: Path):
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret", MAX_WRITE_BYTES="100")

    message = _tool_error(settings, "create_note", {"note_path": "big.md", "content": "x" * 200})

    assert "content is 200 bytes" in message
    assert "MAX_WRITE_BYTES (100)" in message
    assert "append_note" in message


# --- read_note: il budget di caratteri e la lettura per sezione ---

#: The note the section tests read, written as a literal so the expected
#: section text is a known value and not recomputed the way the code splits it.
SECTIONED_NOTE = "# Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n\n## Gamma\n\ngamma body\n"


def _sectioned_vault(tmp_path: Path) -> Settings:
    """A write-enabled vault with one committed note that has two sections.

    Written as bytes so the note on disk is the literal above (a text-mode
    write turns `\\n` into CRLF on Windows, and the section text is compared
    as a literal).
    """
    settings = _write_settings(tmp_path, VAULT_TOKEN="s3cret")
    (settings.vault_root / "sectioned.md").write_bytes(SECTIONED_NOTE.encode("utf-8"))
    _git(settings.vault_root, "add", "-A")
    _git(settings.vault_root, "commit", "-q", "-m", "sectioned note")
    return settings


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_small_note_keeps_exactly_the_shape_it_had(tmp_path: Path):
    settings = _sectioned_vault(tmp_path)

    note = _mcp_call(settings, "read_note", {"note_ref": "sectioned.md"}).structured_content

    assert set(note) == {
        "path", "title", "tags", "size_bytes", "modified_at", "truncated",
        "content", "full_content", "content_hash", "sections",
    }
    assert note["content"] == "# Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n\n## Gamma\n\ngamma body\n"


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_large_note_is_cut_and_says_how_to_read_the_rest(tmp_path: Path):
    settings = _sectioned_vault(tmp_path)

    note = _mcp_call(settings, "read_note", {"note_ref": "sectioned.md", "max_chars": 20}).structured_content

    assert note["content_truncated"] is True
    assert note["content"] == SECTIONED_NOTE[:20]
    assert note["returned_chars"] == 20
    assert note["total_chars"] == len(SECTIONED_NOTE)
    assert "section=" in note["next_step"] and "max_chars" in note["next_step"]
    # the section list survives the cut: it is what makes the next call possible
    assert [section["heading"] for section in note["sections"]] == ["# Alpha", "## Beta", "## Gamma"]


@pytest.mark.skipif(find_spec("uvicorn") is None or find_spec("mcp") is None, reason="the server module needs its own runtime dependencies")
def test_a_section_read_returns_only_that_section_and_its_hash(tmp_path: Path):
    settings = _sectioned_vault(tmp_path)
    sections = {
        section["heading"]: section["content_hash"]
        for section in _mcp_call(settings, "read_note", {"note_ref": "sectioned.md"}).structured_content["sections"]
    }

    section = _mcp_call(settings, "read_note", {"note_ref": "sectioned.md", "section": "## Beta"}).structured_content

    assert section["section"] == "## Beta"
    # a section's span runs to the next heading of the same level, so it keeps
    # the blank line that separates the two: it is exactly what update_section
    # replaces and what its hash covers
    assert section["content"] == "## Beta\n\nbeta body\n\n"
    # the hash is the one update_section compares against, not a hash of the read
    assert section["content_hash"] == sections["## Beta"]

    written = _mcp_call(
        settings,
        "update_section",
        {
            "note_ref": "sectioned.md",
            "section_heading": section["section"],
            "content": "## Beta\n\nrewritten\n",
            "expected_hash": section["content_hash"],
        },
    ).structured_content

    assert written["action"] == "updated_section"
    assert (settings.vault_root / "sectioned.md").read_text(encoding="utf-8").replace("\r\n", "\n") == (
        "# Alpha\n\nalpha body\n\n## Beta\n\nrewritten\n\n## Gamma\n\ngamma body\n"
    )

