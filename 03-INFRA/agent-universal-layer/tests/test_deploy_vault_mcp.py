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
from pathlib import Path

import pytest
import yaml

VAULT_MCP = Path(__file__).resolve().parents[2] / "deploy" / "vault-mcp"
SRC = VAULT_MCP / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vault_mcp_server import __version__ as vault_mcp_version  # noqa: E402
from vault_mcp_server.config import Settings  # noqa: E402
from vault_mcp_server.vault import VaultService, _make_snippet, _redact_snippet  # noqa: E402


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
    assert len(snippet) <= 260  # 200 di finestra + ellissi


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
