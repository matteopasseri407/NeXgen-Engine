#!/usr/bin/env python3
"""Le regole di rilascio, dove si possono provare.

Vivevano dentro blocchi di shell annidati nello YAML della CI: decidevano cosa
il gate anti-fuga controlla e se una versione è accettabile, e l'unico modo di
verificarle era far partire davvero un evento di CI.

Il glob che vive in una shell non è una regola: `[0-9]*.[0-9]*.[0-9]*` accetta
`1.2.3abc` come se fosse semver. Qui la regola è ancorata, ed è provata.
"""
from __future__ import annotations

import argparse
import re
import sys

#: Semver ancorato, con prerelease e build opzionali. Ancorato è il punto:
#: senza ancore, "quasi una versione" passa come una versione.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

#: Lo SHA che GitHub manda quando un branch non aveva un "prima".
ZERO_SHA = "0" * 40


def is_semver(value: str) -> bool:
    """Vero solo se la stringa è una versione, non se le assomiglia."""
    return bool(SEMVER_RE.match(value.strip()))


def version_matches_tag(version: str, tag: str) -> tuple[bool, str]:
    """Un tag di rilascio deve nominare la versione che contiene."""
    version = version.strip()
    tag = tag.strip()
    if not is_semver(version):
        return False, f"VERSION non è una versione valida: '{version}'"
    if f"v{version}" != tag:
        return False, (
            f"l'albero al tag {tag} porta VERSION {version}: "
            f"un tag di rilascio deve nominare la versione che contiene"
        )
    return True, f"{tag} e VERSION {version} concordano"


def scan_range(event_name: str, before: str, sha: str, pr_base: str = "", pr_head: str = "") -> str:
    """L'intervallo di commit che il gate anti-fuga deve esaminare.

    È una decisione di sicurezza, non impalcatura di CI: sbagliarla significa
    non controllare i commit in cui un segreto potrebbe essere entrato.
    """
    if event_name == "pull_request":
        if not pr_base or not pr_head:
            raise ValueError("una pull request deve dichiarare base e head")
        return f"{pr_base}..{pr_head}"
    if not before or before == ZERO_SHA:
        # Primo push su un branch: non esiste un "prima" con cui confrontare.
        return sha
    return f"{before}..{sha}"


def newer_version(left: str, right: str) -> bool:
    """Vero se `left` è più recente di `right`, confrontando numeri e non testo."""
    def parts(v: str) -> tuple[int, ...]:
        core = v.strip().lstrip("v").split("-", 1)[0].split("+", 1)[0]
        return tuple(int(n) for n in core.split(".")[:3])

    return parts(left) > parts(right)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-release",
        description="Le regole di rilascio, invocabili dalla CI e dalla riga di comando.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-version", help="VERSION e il tag concordano?")
    p.add_argument("version")
    p.add_argument("tag")

    p = sub.add_parser("scan-range", help="Quale intervallo di commit va scansionato")
    p.add_argument("--event", required=True)
    p.add_argument("--before", default="")
    p.add_argument("--sha", required=True)
    p.add_argument("--pr-base", default="")
    p.add_argument("--pr-head", default="")

    args = parser.parse_args(argv)

    if args.command == "check-version":
        ok, message = version_matches_tag(args.version, args.tag)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    try:
        print(scan_range(args.event, args.before, args.sha, args.pr_base, args.pr_head))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
