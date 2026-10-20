"""What the engine refuses to execute: one written line instead of a granted exception.

The only source is the executor's own report, and inside it only the request
section of the engine's template. A supervisor's brief, review or corrections are
never read: a phrase about the bump check is the supervisor talking about the
work, not the executor asking for a bump. Fenced code and quoted lines inside the
section are ignored too, so pasting an output is not a request. When the section
is absent nothing is recognised, and the package says so instead of guessing.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.files import secure_artifact

from . import supervision_state as state

#: The file the owner reads: one line per request, never rewritten.
FILE = "07-richieste-proprietario.md"
#: The two words the engine's own report template uses for the request section.
REQUEST_WORDS = ("dubbi", "richieste")
#: What the package says when the report has no request section at all.
NO_SECTION = "sezione delle richieste assente nel resoconto"
#: What the package says when the report is not written yet.
NO_REPORT = "resoconto non ancora scritto: nessuna richiesta letta"

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


def scan(text: str) -> tuple[list[dict], bool]:
    """The declared requests of a report, and whether it has the request section at all."""
    body, found = section(text)
    if not found:
        return [], False
    spoken = speakable(body)
    folded = spoken.casefold()
    hits: list[dict] = []
    for kind, phrases in KINDS:
        for phrase in phrases:
            at = folded.find(phrase.casefold())
            if at < 0:
                continue
            hits.append({"kind": kind, "phrase": phrase, "sentence": _sentence(spoken, at)})
            break
    return hits, True


def section(text: str) -> tuple[str, bool]:
    """The body of the template's request section and whether the report has one.

    The heading is the template's own: both words, in any order and case, on a
    line that is a heading. No heading, no requests: the rest of a report is the
    work, not a message to the owner.
    """
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        title = stripped.lstrip("#").strip().casefold()
        if all(word in title for word in REQUEST_WORDS):
            return "\n".join(_section_body(lines, index)), True
    return "", False


def speakable(body: str) -> str:
    """The section without fenced code blocks and without quoted lines: a paste is not a request."""
    kept: list[str] = []
    fenced = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or stripped.startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def record(task_id: str, unit: str, hits: list[dict], source: str) -> list[dict]:
    """Append the requests not on file yet and return those.

    One line per unit, kind and sentence: the same request written twice, or the
    same report collected twice, changes nothing. The sentence is the key because
    the owner's file must never quote itself back as a new request.
    """
    if not hits:
        return []
    folder = state.open_task(task_id)
    path = folder / FILE
    known = _written(path)
    name = state.slug(unit)
    fresh = [hit for hit in hits if (name, hit["kind"], hit["sentence"]) not in known]
    if not fresh:
        return []
    secure_artifact(folder)
    if not path.is_file():
        path.write_text(_header(), encoding="utf-8")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for hit in fresh:
            handle.write(f"- {_now()} · {name} · {hit['kind']} · {source} · «{hit['sentence']}»\n")
    return fresh


def scan_report(task_id: str, unit: str) -> dict:
    """The executor's report, its requests, and whether the request section exists.

    This is the only entry point: the brief, the reviews and the corrections are
    the supervisor's words and are never scanned.
    """
    report = state.open_task(task_id) / f"30-resoconto-{state.slug(unit)}.md"
    if not report.is_file():
        return {"report": "", "found": False, "hits": [], "requests": []}
    hits, found = scan(report.read_text(encoding="utf-8"))
    written = record(task_id, unit, hits, report.name) if found else []
    return {"report": report.name, "found": found, "hits": hits, "requests": written}


def read(task_id: str) -> list[str]:
    """The lines already written, for a person who wants to see them again."""
    path = state.open_task(task_id) / FILE
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def section_status(result: dict) -> str:
    """Why the section could not be read, or an empty string when it was there (or not due yet)."""
    if result.get("report") and not result.get("found"):
        return NO_SECTION
    return ""


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
    """The (unit, kind, sentence) already on file, read back from its own lines."""
    if not path.is_file():
        return set()
    known: set[tuple[str, str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.removeprefix("- ").split(" · ", 4)
        if len(parts) == 5:
            known.add((parts[1], parts[2], parts[4].strip().strip("«»")))
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
