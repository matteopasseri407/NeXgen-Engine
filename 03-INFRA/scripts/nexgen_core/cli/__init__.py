#!/usr/bin/env python3
"""L'unico entrypoint del motore: `nexgen`.

Prima c'erano tredici comandi separati con nomi ereditati da versioni diverse.
Ora c'è un verbo per ogni cosa che una persona può chiedere, e i vecchi nomi
continuano a funzionare come alias, perché una macchina già installata li
invoca per percorso e romperli significa romperla a metà aggiornamento.

I gruppi di verbi vivono in moduli separati e si registrano qui. Aggiungere un
comando è aggiungere una riga in un modulo, non toccare questo file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import engine, skill_cmds, tool_cmds, vault_cmds

#: I gruppi di verbi, nell'ordine in cui compaiono nell'aiuto.
GROUPS = (engine, vault_cmds, skill_cmds, tool_cmds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexgen",
        description="NeXgen Engine — tiene allineate le tue macchine e i tuoi assistenti.",
    )
    sub = parser.add_subparsers(dest="command", metavar="comando")
    for group in GROUPS:
        group.register(sub)
    return parser


def _run_cli(argv: list[str]) -> int:
    parser = build_parser()
    # Gli argomenti destinati a un sottoprogramma non sono affari di questo
    # parser: li raccoglie e li consegna intatti a chi li inoltra. Analizzarli
    # qui significherebbe rifiutare ogni flag che il sottoprogramma aggiunge.
    args, passthrough = parser.parse_known_args(argv)
    args.passthrough = passthrough

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return func(args)


def main(argv: list[str] | None = None) -> int:
    """Punto di ingresso, con la rete sotto: un errore non stampa un traceback."""
    if argv is None:
        argv = sys.argv[1:]
    try:
        return _run_cli(argv)
    except KeyboardInterrupt:
        print("\nInterrotto.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
