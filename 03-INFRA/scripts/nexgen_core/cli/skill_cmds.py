"""The verbs on skills: find one when you need it, and materialize all of them.

Finding and showing a skill on demand is what makes lazy loading usable: if
a capability exists but can't be searched, it might as well not exist.
"""
from __future__ import annotations

from nexgen_core.i18n import t


def register(sub) -> None:
    # `skills` too: `nexgen info` and the docs have always said `skills bump`.
    p = sub.add_parser("skill", aliases=["skills"], help=t("Find, show, and materialize skills"))
    ssub = p.add_subparsers(dest="skill_command", metavar="verb")

    q = ssub.add_parser("list", help=t("List the managed skills"))
    q.set_defaults(func=lambda a: _forward(["list"]))

    q = ssub.add_parser("find", help=t("Search for a skill by name or description"))
    q.add_argument("terms", nargs="+", help=t("One or more terms, ANDed together"))
    q.set_defaults(func=lambda a: _forward(["find", *a.terms]))

    q = ssub.add_parser("show", help=t("Show a skill's body"))
    q.add_argument("name")
    q.set_defaults(func=lambda a: _forward(["show", a.name]))

    q = ssub.add_parser("path", help=t("Print a skill's file path"))
    q.add_argument("name")
    q.set_defaults(func=lambda a: _forward(["path", a.name]))

    q = ssub.add_parser("sync", aliases=["apply"], help=t("Materialize the declared skills"))
    q.add_argument("--migrate-legacy", action="store_true", help=t("Quarantine the inherited views"))
    q.set_defaults(func=lambda a: _forward(["apply"] + (["--migrate-legacy"] if a.migrate_legacy else [])))

    q = ssub.add_parser("bump", help=t("Raise guardian-vetted third-party pins with one approval"))
    q.set_defaults(func=lambda a: _forward(["bump"]))

    q = ssub.add_parser("adopt", help=t("Follow the engine for the skills it ships, instead of keeping frozen copies in the Vault"))
    q.add_argument("names", nargs="*", help=t("Skills to adopt (default: list them)"))
    q.add_argument("--all", action="store_true", help=t("Every copy identical to the engine's"))
    q.add_argument("--force", action="store_true", help=t("Also adopt a copy that differs (it is kept in a backup folder)"))
    q.add_argument("--dry-run", action="store_true", help=t("Show what would change, change nothing"))
    q.set_defaults(func=lambda a: __import__("nexgen_core.skill_adopt", fromlist=["main"]).main(
        a.names, all_=a.all, force=a.force, dry_run=a.dry_run))

    q = ssub.add_parser("validate", help=t("Check the manifest without writing anything"))
    q.set_defaults(func=lambda a: _forward(["validate"]))

    q = ssub.add_parser("index", help=t("Regenerate the skill catalog"))
    q.set_defaults(func=lambda a: _forward(["index"]))

    p.set_defaults(func=lambda a: _usage(p))


def _usage(parser) -> int:
    parser.print_help()
    return 0


def _forward(argv: list[str]) -> int:
    from nexgen_core.skills import main as skills_main

    return skills_main(argv)
