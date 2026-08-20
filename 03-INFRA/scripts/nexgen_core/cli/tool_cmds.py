"""Gli utensili: l'ora vera, una cartella da aprire, il browser, la ricerca.

Sono comandi piccoli che il motore distribuisce perché gli agenti li usano.
Ognuno resta un modulo a sé: qui c'è solo l'aggancio al dispatcher.
"""
from __future__ import annotations


def _all(args) -> list[str]:
    """Gli argomenti che il dispatcher non ha riconosciuto, nel loro ordine."""
    return list(getattr(args, "passthrough", []) or [])


def register(sub) -> None:
    p = sub.add_parser("tool", help="Utensili distribuiti con il motore")
    tsub = p.add_subparsers(dest="tool_command", metavar="utensile")

    q = tsub.add_parser("now", help="L'ora locale attendibile, con lo stato della sincronizzazione")
    q.set_defaults(func=lambda a: _run("now", _all(a)))

    q = tsub.add_parser("open", help="Apri una cartella nel gestore file del sistema")
    q.set_defaults(func=lambda a: _run("open_folder", _all(a)))

    q = tsub.add_parser("chrome", help="Avvia o rianima Chrome sulla porta di debug")
    q.set_defaults(func=lambda a: _run("chrome", _all(a)))

    q = tsub.add_parser("firecrawl", help="Ricerca e scraping tramite l'istanza locale")
    q.set_defaults(func=lambda a: _run("firecrawl", _all(a)))

    p.set_defaults(func=lambda a: _usage(p))

    # Il council non è un utensile: è una richiesta a sé, e resta di primo livello.
    c = sub.add_parser("council", help="Convoca una revisione fra modelli di vendor diversi")
    c.set_defaults(func=cmd_council)


def _usage(parser) -> int:
    parser.print_help()
    return 0


def _run(module_name: str, argv: list[str]) -> int:
    import importlib

    module = importlib.import_module(f"nexgen_core.tools.{module_name}")
    return module.main(argv)


def cmd_council(args) -> int:
    from nexgen_core.tools.council import main as council_main

    return council_main(_all(args))
