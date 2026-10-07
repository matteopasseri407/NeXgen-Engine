"""Il vault-mcp spedito nel deploy: promesse verificabili, non raccontate.

Tre regole:
1. il tag dell'immagine nel compose segue `__version__` (il commento del
   compose lo promette da due release; il test non esisteva, ora esiste);
2. l'indice di lettura esclude 99-SECRETS per default, come il write path:
   search/read su un albero segreti è esattamente la via di recupero che le
   regole del vault vietano;
3. gli snippet di search sono orientamento, non fedeltà: tutto ciò che ha
   forma di segreto esce mascherato.
"""
from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path

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
