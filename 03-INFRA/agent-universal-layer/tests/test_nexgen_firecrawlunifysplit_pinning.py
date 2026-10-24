"""Pinning del ritiro della superficie `firecrawl-local`.

Il wrapper era insieme un verbo del CLI, un alias storico installato su ogni
piattaforma, un punto d'ingresso del pacchetto e una voce della tabella di sync.
Il connettore è un server MCP, quindi quella superficie non deve poter
rientrare da una di quelle porte. Solo introspezione del parser e del
filesystem: nessun processo, nessuna rete.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import build_parser  # noqa: E402
from nexgen_core.shims import COMMANDS, LEGACY_ALIASES, install_shims  # noqa: E402

#: Le voci di traduzione che esistevano solo per l'aiuto del wrapper.
RETIRED_TRANSLATIONS = ("Firecrawl Local CLI (v2)", "Check the service status", "local default")

#: I file che potrebbero riportare l'alias in vita.
ALIAS_TABLES = ("nexgen_core/shims.py", "nexgen_core/entrypoints.py", "agent_sync.py")


def _subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    """The named sub-command of a parser, without starting anything."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return action.choices[name]
    raise AssertionError(f"nessun sottocomando '{name}'")


def test_il_verbo_firecrawl_non_e_piu_un_sottocomando() -> None:
    tools = _subparser(build_parser(), "tool")
    verbs = {action.dest for action in tools._actions if isinstance(action, argparse._SubParsersAction)}
    choices = next(
        action.choices for action in tools._actions if isinstance(action, argparse._SubParsersAction)
    )
    assert "firecrawl" not in choices
    assert {"now", "open", "chrome", "update-notifier"} <= set(choices)
    assert verbs, "il verbo tool non ha sottocomandi: la verifica sarebbe vuota"


def test_nessun_alias_storico_si_chiama_firecrawl_local() -> None:
    assert "firecrawl-local" not in LEGACY_ALIASES
    assert "firecrawl-local" not in dict(COMMANDS)
    assert "agent-sync" in LEGACY_ALIASES and "council" in LEGACY_ALIASES


def test_il_pacchetto_non_registra_l_entry_point() -> None:
    entrypoints = importlib.import_module("nexgen_core.entrypoints")
    assert not hasattr(entrypoints, "firecrawl_local")
    assert not [name for name in dir(entrypoints) if "firecrawl" in name]
    assert hasattr(entrypoints, "agent_sync") and hasattr(entrypoints, "council")


def test_il_sync_non_installa_lo_shim() -> None:
    agent_sync = importlib.import_module("agent_sync")
    assert "firecrawl-local" not in agent_sync.LINKED_COMMANDS
    assert "firecrawl-search-health" not in str(agent_sync.LINKED_COMMANDS)
    assert "agent-sync" in agent_sync.LINKED_COMMANDS


@pytest.mark.parametrize("source, name", [(path, "firecrawl-local") for path in ALIAS_TABLES])
def test_nessuna_tabella_di_alias_nomina_il_wrapper(source: str, name: str) -> None:
    text = (SCRIPTS_DIR / source).read_text(encoding="utf-8")
    assert name not in text
    assert "agent-sync" in text, f"{source} non è la tabella degli alias"


@pytest.mark.parametrize("text", RETIRED_TRANSLATIONS)
def test_nessuna_traduzione_porta_l_aiuto_del_wrapper(text: str) -> None:
    from nexgen_core import i18n

    catalog = i18n._load_catalog("it")
    assert text not in catalog
    assert len(catalog) > 200, "il catalogo italiano non si è caricato"


def test_il_sync_scrive_gli_alias_rimasti_e_non_il_wrapper(tmp_path: Path) -> None:
    """L'installer materializza la tabella: una voce in meno si vede qui, non sulla macchina."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    install_shims(bin_dir=bin_dir, home=tmp_path / "home")

    written = {path.name for path in bin_dir.iterdir()}
    assert any(name.startswith("agent-sync") for name in written)
    assert not [name for name in written if "firecrawl" in name]
    assert len(written) >= len(LEGACY_ALIASES)


def test_l_aiuto_del_verbo_tool_non_promette_piu_la_ricerca() -> None:
    parser = build_parser()
    assert "firecrawl" not in parser.format_help().lower()
    tools_help = _subparser(parser, "tool").format_help().lower()
    assert "firecrawl" not in tools_help
    assert "now" in tools_help
