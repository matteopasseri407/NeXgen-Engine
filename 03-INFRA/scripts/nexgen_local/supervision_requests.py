"""What the engine refuses to execute: one written line instead of a granted exception.

An executor's report or a supervisor's corrections may ask for a force-push, a
tag, a version bump or a change to an existing test. The engine does none of
them and does not try to read intent either: it recognises a short, declared
list of phrases in the report's request section and appends one line per
request to `07-richieste-proprietario.md`. The decision stays the owner's, and
it is written down rather than quietly satisfied.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.files import secure_artifact

from . import supervision_state as state

#: The file the owner reads: one line per request, never rewritten.
FILE = "07-richieste-proprietario.md"
#: The heading the engine's own report template uses for requests; the phrases
#: are looked for there, so a report that quotes the prohibition is not a request.
REQUEST_HEADING = ("richiest", "dubbi", "serve al proprietario")

#: Declared phrases, Italian and English, one kind each. Short on purpose.
KINDS = (
    ("force-push", (
        "force-push", "force push", "--force", "con la forza", "riscrivere la storia",
    )),
    ("tag", (
        "git tag", "creare un tag", "crea un tag", "nuovo tag", "taggare", "tag firmato",
    )),
    ("bump", (
        "bump", "alzare la versione", "alza la versione", "aggiornare version",
        "aggiorna version", "cambiare version", "changelog",
    )),
    ("test esistenti", (
        "modificare i test esistenti", "cambiare i test esistenti", "cancellare un test",
        "togliere un test", "indebolire un test", "saltare un test", "disabilitare un test",
    )),
)


def scan(text: str) -> list[dict]:
    """Every declared request in `text`, one entry per kind, with the sentence that says it."""
    body = request_section(text)
    folded = body.casefold()
    found: list[dict] = []
    for kind, phrases in KINDS:
        for phrase in phrases:
            at = folded.find(phrase.casefold())
            if at < 0:
                continue
            found.append({"kind": kind, "phrase": phrase, "sentence": _sentence(body, at)})
            break
    return found


def request_section(text: str) -> str:
    """The report's request section; the whole text when the report has no such heading."""
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            continue
        title = line.lstrip("#").strip().casefold()
        if any(marker in title for marker in REQUEST_HEADING):
            return "\n".join(_section_body(lines, index))
    return text or ""


def record(task_id: str, unit: str, source: str, text: str) -> list[dict]:
    """Append the requests `text` makes and return the ones that were not written yet.

    One line per unit, kind and source: a report collected twice does not write
    the same request twice, and a new wording of the same request changes nothing.
    """
    found = scan(text)
    if not found:
        return []
    folder = state.open_task(task_id)
    path = folder / FILE
    known = _written(path)
    name = state.slug(unit)
    fresh = [hit for hit in found if (name, hit["kind"], source) not in known]
    if not fresh:
        return []
    secure_artifact(folder)
    if not path.is_file():
        path.write_text(_header(), encoding="utf-8")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for hit in fresh:
            handle.write(
                f"- {_now()} · {name} · {hit['kind']} · {source} · «{hit['sentence']}»\n"
            )
    return fresh


def scan_report(task_id: str, unit: str) -> list[dict]:
    """The requests the unit's own report makes, or nothing when it is not written yet."""
    report = state.open_task(task_id) / f"30-resoconto-{state.slug(unit)}.md"
    if not report.is_file():
        return []
    return record(task_id, unit, report.name, report.read_text(encoding="utf-8"))


def read(task_id: str) -> list[str]:
    """The lines already written, for a person who wants to see them again."""
    path = state.open_task(task_id) / FILE
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def _section_body(lines: list[str], start: int) -> list[str]:
    """The lines under one heading, up to the next heading of the same or a higher level."""
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    body: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            depth = len(stripped) - len(stripped.lstrip("#"))
            if depth <= level:
                break
        body.append(line)
    return body


def _sentence(body: str, at: int) -> str:
    """The sentence around a match, one line and bounded, so the file stays readable."""
    start = max(body.rfind("\n", 0, at), body.rfind(". ", 0, at)) + 1
    end = min(
        [value for value in (body.find("\n", at), body.find(". ", at)) if value >= 0] or [len(body)]
    )
    text = " ".join(body[start:end].split())
    return (text or body[at:at + 120]).strip()[:240]


def _written(path: Path) -> set[tuple[str, str, str]]:
    """The (unit, kind, source) already on file, read back from its own lines."""
    if not path.is_file():
        return set()
    known: set[tuple[str, str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.removeprefix("- ").split(" · ")
        if len(parts) >= 4:
            known.add((parts[1], parts[2], parts[3]))
    return known


def _now() -> str:
    return state.now_iso()


def _header() -> str:
    return (
        "# Richieste al proprietario\n"
        "\n"
        "Il motore non esegue queste richieste: le scrive qui e va avanti.\n"
        "Una riga per richiesta: quando, unità, cosa, dove è stato letto, la frase.\n"
        "\n"
    )
