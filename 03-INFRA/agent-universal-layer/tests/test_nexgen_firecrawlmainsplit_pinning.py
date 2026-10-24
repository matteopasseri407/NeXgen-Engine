"""Pinning del ritiro del client Firecrawl del motore.

Il connettore è il server MCP del fornitore contro l'API cloud: il motore non
parla più l'API HTTP di Firecrawl e non ha più un wrapper `firecrawl-local`.
Questi test fissano che il client, il suo gemello di salute e i loro indirizzi
siano spariti davvero, così che non rientrino per caso da una parte dimenticata.
Solo filesystem e import: nessun processo lanciato, nessuna rete, nessuna chiave.
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
INFRA = SCRIPTS_DIR.parent
CORE = SCRIPTS_DIR / "nexgen_core"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

#: Il modulo e il suo gemello: nati per interrogare un backend self-hosted.
RETIRED_MODULES = ("nexgen_core.tools.firecrawl", "nexgen_core.tools.firecrawl_health")

#: I nomi che solo quel backend conosceva: un client che li nomina è tornato.
RETIRED_MARKERS = ("FIRECRAWL_API_URL", "FIRECRAWL_TUNNEL_PORT", "33002", "/v2/scrape", "/v2/search")

#: Gli script che il wrapper installava come comandi.
RETIRED_SCRIPTS = ("firecrawl-search-health.py", "firecrawl-local.sh", "firecrawl-local.ps1")


def _python_sources(*roots: Path) -> list[Path]:
    return [path for root in roots for path in root.rglob("*.py") if "__pycache__" not in path.parts]


@pytest.mark.parametrize("dotted", RETIRED_MODULES)
def test_il_client_e_il_suo_gemello_non_esistono_piu(dotted: str) -> None:
    with pytest.raises(ImportError):
        importlib.import_module(dotted)
    assert importlib.util.find_spec(dotted) is None
    assert not (CORE / "tools" / f"{dotted.rsplit('.', 1)[-1]}.py").exists()
    assert "firecrawl" not in dir(importlib.import_module("nexgen_core.tools"))


@pytest.mark.parametrize("marker", RETIRED_MARKERS)
def test_nessun_sorgente_del_motore_nomina_il_backend_ritirato(marker: str) -> None:
    sources = _python_sources(SCRIPTS_DIR)
    hits = [str(path.relative_to(INFRA)) for path in sources if marker in path.read_text(encoding="utf-8")]
    assert hits == [], f"{marker} compare ancora in {hits}"
    assert len(sources) > 100, "la scansione non ha letto i sorgenti: sarebbe vuota"


@pytest.mark.parametrize("name", RETIRED_SCRIPTS)
def test_gli_script_del_wrapper_non_ci_sono_piu(name: str) -> None:
    assert not (SCRIPTS_DIR / name).exists()
    assert name not in {path.name for path in SCRIPTS_DIR.iterdir()}


def test_il_pacchetto_non_espone_un_modulo_firecrawl() -> None:
    """Anche un file rimasto in una cache di build non deve diventare importabile."""
    tools = importlib.import_module("nexgen_core.tools")
    names = {module.name for module in pkgutil.iter_modules(tools.__path__)}
    assert not [name for name in names if "firecrawl" in name]
    assert names, "il pacchetto non elenca moduli: la verifica sarebbe vuota"


def test_il_vecchio_backend_locale_non_e_piu_un_default() -> None:
    """La chiave finta e l'indirizzo locale del self-hosted non sono più da nessuna parte."""
    sources = _python_sources(SCRIPTS_DIR)
    assert not [p for p in sources if "local-self-hosted" in p.read_text(encoding="utf-8")]
    assert not [p for p in sources if "127.0.0.1:33002" in p.read_text(encoding="utf-8")]
    assert "127.0.0.1:33002" not in (INFRA.parent / "docs" / "codebase-map.md").read_text(encoding="utf-8")


def test_la_mappa_del_codice_non_elenca_il_wrapper() -> None:
    """Una mappa che nomina file cancellati manda chi la legge a cercare fantasmi."""
    text = (INFRA.parent / "docs" / "codebase-map.md").read_text(encoding="utf-8")
    assert "tools/firecrawl.py" not in text
    assert "tools/firecrawl_health.py" not in text
    assert "firecrawl-search-health.py" not in text
    assert "03-INFRA/deploy/firecrawl/" not in text
