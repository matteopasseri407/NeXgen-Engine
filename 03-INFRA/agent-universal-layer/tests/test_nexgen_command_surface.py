"""La rete: ogni comando che il motore installa viene eseguito davvero.

Questa suite esiste perché la sua assenza è costata cara. Un test dichiarava
`vault-groom` risolto verificando che il file esistesse, avesse lo shebang e
comparisse in una lista. Il comando, eseguito, andava in TypeError alla prima
riga — con qualunque argomento, incluso nessuno. Un documento ufficiale lo
certificava come "RISOLTO".

La regola qui è una sola: se un nome finisce in `~/.local/bin`, qualcuno lo
invoca. Nessun test in questo file controlla che un file esista.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import build_parser, main as nexgen_main
from nexgen_core.shims import LEGACY_ALIASES, PRIMARY

#: Quanto si concede a un comando per stampare il proprio aiuto. Un comando
#: che ci mette di più sta facendo lavoro che l'aiuto non dovrebbe fare.
HELP_TIMEOUT_SECONDS = 30


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    entry = SCRIPTS_DIR / "nexgen_core" / "cli" / "__init__.py"
    return subprocess.run(
        [sys.executable, str(entry), *argv],
        capture_output=True, text=True, check=False, timeout=HELP_TIMEOUT_SECONDS,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent-home-for-tests"},
    )


def _verbs() -> list[str]:
    """I verbi di primo livello, letti dal parser invece che elencati a mano."""
    parser = build_parser()
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - argparse non offre altro
        return sorted(action.choices)
    return []


def test_the_parser_declares_verbs_at_all():
    assert _verbs(), "nessun verbo registrato: il rilevamento è rotto, non il codice"


@pytest.mark.parametrize("verb", _verbs())
def test_every_verb_answers_for_help_without_a_traceback(verb: str):
    """Nessun verbo può morire prima di riuscire a spiegarsi.

    È esattamente il difetto che vault-groom aveva: non un errore di logica
    dentro il comando, ma un comando che non arriva a partire.
    """
    res = _run([verb, "--help"])
    assert "Traceback" not in res.stderr, f"'{verb} --help' è esploso:\n{res.stderr}"
    assert res.returncode == 0, f"'{verb} --help' esce con {res.returncode}:\n{res.stderr}"
    assert res.stdout.strip(), f"'{verb} --help' non stampa niente"


@pytest.mark.parametrize("alias", sorted(LEGACY_ALIASES))
def test_every_historical_name_still_reaches_a_real_command(alias: str):
    """Un nome ereditato che smette di funzionare lascia una macchina a metà.

    L'aggiornamento alla versione nuova lo esegue il comando della versione
    vecchia: se il nome non risolve più, l'aggiornamento si ferma a metà strada.
    """
    prefix = LEGACY_ALIASES[alias]
    res = _run([*prefix, "--help"])
    assert "Traceback" not in res.stderr, f"l'alias '{alias}' è esploso:\n{res.stderr}"
    assert res.returncode == 0, f"l'alias '{alias}' esce con {res.returncode}:\n{res.stderr}"


def test_the_primary_command_is_the_one_the_aliases_point_at():
    assert PRIMARY == "nexgen"
    assert PRIMARY not in LEGACY_ALIASES, "il comando vero non può essere anche un suo alias"


def test_no_argument_prints_help_and_succeeds():
    res = _run([])
    assert res.returncode == 0
    assert "usage:" in res.stdout.lower()


def test_an_unknown_verb_fails_without_a_traceback():
    res = _run(["questo-verbo-non-esiste"])
    assert res.returncode != 0
    assert "Traceback" not in res.stderr


def test_the_in_process_entry_point_matches_the_subprocess_one(capsys):
    """`main([])` e l'invocazione da riga di comando devono comportarsi uguale."""
    assert nexgen_main([]) == 0
    assert "usage:" in capsys.readouterr().out.lower()
