"""I verbi sulle skill: trovarne una quando serve, e materializzarle tutte.

Trovare e mostrare una skill su richiesta è ciò che rende usabile il
caricamento pigro: se una capacità esiste ma non si può cercare, tanto vale
che non ci sia.
"""
from __future__ import annotations


def register(sub) -> None:
    p = sub.add_parser("skill", help="Trova, mostra e materializza le skill")
    ssub = p.add_subparsers(dest="skill_command", metavar="verbo")

    q = ssub.add_parser("list", help="Elenca le skill gestite")
    q.set_defaults(func=lambda a: _forward(["list"]))

    q = ssub.add_parser("find", help="Cerca una skill per nome o descrizione")
    q.add_argument("terms", nargs="+", help="Uno o più termini, in AND")
    q.set_defaults(func=lambda a: _forward(["find", *a.terms]))

    q = ssub.add_parser("show", help="Mostra il corpo di una skill")
    q.add_argument("name")
    q.set_defaults(func=lambda a: _forward(["show", a.name]))

    q = ssub.add_parser("path", help="Stampa il percorso del file di una skill")
    q.add_argument("name")
    q.set_defaults(func=lambda a: _forward(["path", a.name]))

    q = ssub.add_parser("sync", aliases=["apply"], help="Materializza le skill dichiarate")
    q.add_argument("--migrate-legacy", action="store_true", help="Metti in quarantena le viste ereditate")
    q.set_defaults(func=lambda a: _forward(["apply"] + (["--migrate-legacy"] if a.migrate_legacy else [])))

    q = ssub.add_parser("validate", help="Controlla il manifest senza scrivere niente")
    q.set_defaults(func=lambda a: _forward(["validate"]))

    q = ssub.add_parser("index", help="Rigenera il catalogo delle skill")
    q.set_defaults(func=lambda a: _forward(["index"]))

    p.set_defaults(func=lambda a: _usage(p))


def _usage(parser) -> int:
    parser.print_help()
    return 0


def _forward(argv: list[str]) -> int:
    from nexgen_core.skills import main as skills_main

    return skills_main(argv)
