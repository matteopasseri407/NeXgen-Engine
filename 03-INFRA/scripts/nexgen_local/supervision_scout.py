"""The explorer's round: one fixed copy, one written brief, one visible plan.

One job: give `agy` a throwaway copy of the unit's repository at a fixed,
already-trusted path, point it at a brief file, and check afterwards that the
copy came back exactly as it was. The copy is recreated at every round, so the
scout never inherits yesterday's edits, and it is detached: there is no branch
whose work could later be mistaken for the executor's. A tracked file that
changed, or a HEAD that moved, fails the round; an untracked file is named in
the journal and ignored, because what matters is the code, not the caches a tool
leaves behind.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from nexgen_core.discipline.gitutil import GitError, git

from . import supervision_adapters as adapters
from . import supervision_policy as policy
from . import supervision_state as state
from . import supervision_trust as trust
from . import supervision_windows as windows
from .supervision_state import SupervisionError

#: The fixed folder of the scout, the same at every round so the trust entry stays one.
FOLDER = "24-ricognizione"
#: The brief the scout reads and the report it writes, both in the task folder.
BRIEF = "25-brief-ricognizione.md"
REPORT = "26-rapporto-ricognizione-{round}.md"
#: How long the scout may take before the tab is left alone; it keeps running, the engine stops waiting.
PRINT_TIMEOUT_S = 3600.0
#: What a finished round carries over from the round it closes.
ROUND_FIELDS = ("round", "folder", "base", "head", "brief", "report", "window", "trusted")


class ScoutError(SupervisionError):
    """No unit to copy from, no explorer installed, a round that cannot be prepared."""


def open_round(
    task_id: str, question: str, *, opener=None, settings: str | Path | None = None,
    harness: str = "",
) -> dict:
    """Recreate the fixed copy, write the brief, trust the folder, open the round in a tab.

    The harness is the one the lane gives the scout role, unless the owner names
    one for this round. It is resolved before the copy is made: a harness that
    cannot scout must not leave a folder and a brief behind.
    """
    text = (question or "").strip()
    if not text:
        raise ScoutError("brief vuoto: scrivi in una riga cosa deve guardare il ricognitore")
    chosen = _scout_harness(harness)
    repo, base = _source(task_id)
    folder = _folder(task_id)
    _recreate(repo, base, folder)
    round_no = _rounds(task_id) + 1
    report = state.open_task(task_id) / REPORT.format(round=round_no)
    brief = state.open_task(task_id) / BRIEF
    brief.write_text(_brief_text(task_id, text, report, base), encoding="utf-8")
    trusted = trust.ensure(folder, settings=settings)
    argv = [sys.executable, "-c", _bootstrap(task_id, round_no)]
    handle = (opener or windows.open_window)(
        f"ricognitore · {state.slug(text)}", argv, cwd=folder,
        marker=windows.tab_marker(task_id, "scout"),
    )
    record = {
        "task": task_id, "round": round_no, "folder": str(folder), "base": base,
        "head": _head(folder), "brief": str(brief), "report": str(report),
        "window": str(handle.get("title", "")), "trusted": bool(trusted["added"]),
        "harness": chosen,
    }
    state.append_event(task_id, "scout_opened", **record)
    return record


def _scout_harness(chosen: str) -> str:
    """The adapter this round runs on, refused with its declared absence instead of opening nothing."""
    name = (chosen or "").strip() or policy.harness_for("ricognitore")
    try:
        adapter = adapters.get(name)
    except adapters.base.AdapterError as exc:
        raise ScoutError(str(exc)) from exc
    try:
        adapter.require("scout", "un ricognitore che non sa leggere non apre nessun giro")
    except adapters.base.AdapterError as exc:
        raise ScoutError(str(exc)) from exc
    return adapter.name


#: What the tab tells the explorer, word for word. `agy` runs in `--mode plan`, and
#: with a long brief it wrote a plan, asked "Proceed" and exited without a report
#: (2026-10-24). A prompt that says the report is the only deliverable ran three
#: rounds out of three that night, so the refusal to plan is stated, not implied.
SCOUT_PROMPT = (
    "Leggi il brief in {brief} e segui solo quello. "
    "Non scrivere un piano e non chiedere conferma: esegui subito quello che il brief chiede "
    "e scrivi il rapporto in {report}. Non modificare nessun file di codice."
)


def run_in_tab(task_id: str, round_no: int = 0) -> int:
    """The tab side: run the scout's harness in plan mode, then say whether the copy stayed clean."""
    record = _opened(task_id, round_no)
    report = Path(record["report"])
    name = str(record.get("harness") or "agy")
    try:
        adapter = adapters.get(name)
    except adapters.base.AdapterError as exc:
        print(f"ricognitore non identificato: {exc}", file=sys.stderr)
        return 1
    executable = shutil.which(adapter.executable)
    if not executable:
        print(f"ricognitore non installato: `{adapter.executable}` non è nel PATH", file=sys.stderr)
        return 1
    print(f"ricognitore ({adapter.name}), giro {record['round']}: {record['folder']}")
    print(f"brief {record['brief']}")
    print(f"rapporto atteso {report}")
    print("-" * 60, flush=True)
    command = adapter.scout_argv(
        executable,
        SCOUT_PROMPT.format(brief=record["brief"], report=report),
    )
    try:
        subprocess.run(command, cwd=record["folder"], check=False, timeout=PRINT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"ricognitore interrotto: {type(exc).__name__}", file=sys.stderr)
    code = _after(task_id, record, report)
    print(windows.close_tab(task_id, "scout")["line"], flush=True)
    return code


def verdict(task_id: str, round_no: int = 0) -> dict:
    """What the last round left behind, without running anything: the check, callable alone."""
    record = _opened(task_id, round_no)
    return _check(record, Path(record["report"]))


def _after(task_id: str, record: dict, report: Path) -> int:
    """Journal and print the end of the round: the copy untouched, the report where it belongs."""
    found = _check(record, report)
    fields = {key: record[key] for key in ROUND_FIELDS if key in record}
    fields.update(found)
    state.append_event(task_id, "scout_finished", **fields)
    print("-" * 60)
    print(f"copia {found['folder']}: {'pulita' if found['clean'] else 'SPORCA'}")
    for line in found["changes"]:
        print(f"  {line}")
    for line in found.get("untracked") or []:
        print(f"  non tracciato, ignorato: {line}")
    print(f"rapporto: {'scritto' if found['report_written'] else 'NON scritto'} ({found['report']})")
    return 0 if found["clean"] else 1


def _check(record: dict, report: Path) -> dict:
    """Whether the copy is where it started and holds nothing new, and whether the report exists.

    An untracked file is named and not counted: the scout writes nothing, but a
    cache or a lock the explorer leaves behind is not a change to the code. A
    tracked file that changed, or a HEAD that moved, is dirty and fails the round.
    """
    folder = Path(record["folder"])
    try:
        status = [line for line in git(folder, "status", "--porcelain").splitlines() if line.strip()]
        head = _head(folder)
    except (GitError, OSError) as exc:
        return {
            "folder": str(folder), "clean": False, "changes": [f"git non leggibile ({exc})"],
            "untracked": [], "head": "", "report": str(report), "report_written": report.is_file(),
        }
    changes = [line for line in status if not line.startswith("??")]
    untracked = [line[3:] for line in status if line.startswith("??")]
    moved = head != record["head"]
    return {
        "folder": str(folder),
        "clean": not changes and not moved,
        "changes": changes + ([f"HEAD {record['head'][:12]} -> {head[:12]}"] if moved else []),
        "untracked": untracked,
        "head": head,
        "report": str(report),
        "report_written": report.is_file() and bool(report.read_text(encoding="utf-8").strip()),
    }


def _folder(task_id: str) -> Path:
    """The one path the scout always works in, inside the task folder."""
    return state.open_task(task_id) / FOLDER


def _source(task_id: str) -> tuple[str, str]:
    """The repository and base of the task's first unit: the scout reads the code the unit works on."""
    for row in state.read_events(task_id):
        if row.get("event") == "unit_created" and row.get("repo"):
            return str(row["repo"]), str(row.get("base") or "HEAD")
    raise ScoutError(
        "nessuna unità in questo compito: la ricognizione parte dal repository della prima unità "
        "(`sup unit ...` una volta, poi `sup scout`)"
    )


def _recreate(repo: str, base: str, folder: Path) -> None:
    """A fresh detached copy at every round: nothing of the previous one survives."""
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    try:
        git(Path(repo), "clone", "--quiet", "--no-hardlinks", repo, str(folder))
        git(folder, "checkout", "--quiet", "--detach", base)
    except GitError as exc:
        raise ScoutError(f"copia della ricognizione non creata: {exc}") from exc


def _rounds(task_id: str) -> int:
    """How many rounds this task already opened."""
    return len([row for row in state.read_events(task_id) if row.get("event") == "scout_opened"])


def _opened(task_id: str, round_no: int = 0) -> dict:
    """The round the tab belongs to: the one asked for, or the last one opened."""
    rows = [row for row in state.read_events(task_id) if row.get("event") == "scout_opened"]
    if not rows:
        raise ScoutError(f"nessun giro di ricognizione aperto sul compito {task_id} (`sup scout {task_id} \"...\"`)")
    if round_no:
        rows = [row for row in rows if int(row.get("round") or 0) == int(round_no)]
        if not rows:
            raise ScoutError(f"giro di ricognizione {round_no} sconosciuto sul compito {task_id}")
    return rows[-1]


def _head(folder: Path) -> str:
    """The commit the copy is on right now."""
    return git(folder, "rev-parse", "HEAD").strip()


def _brief_text(task_id: str, question: str, report: Path, base: str) -> str:
    """The scout's brief: the question, the write target, and the one thing it may not do."""
    return (
        f"# Brief della ricognizione — compito {task_id}\n\n"
        "Sei il ricognitore. Lavori su una copia staccata del repository, in sola lettura:\n"
        "nessuna modifica al codice, nessun commit, nessuna push.\n\n"
        "## Domanda\n\n"
        f"{question}\n\n"
        "## Rapporto\n\n"
        f"Scrivi il rapporto completo in:\n\n{report}\n\n"
        "Il rapporto è l'unico risultato: non serve un piano, non serve una conferma.\n\n"
        "Rispondi con i fatti che hai letto nel codice, con i percorsi esatti. "
        "Se qualcosa non è verificabile da qui, scrivilo invece di supporlo.\n\n"
        f"## Base\n\nLa copia è al commit {base}, staccata: non appartiene a nessun ramo.\n"
    )


def _bootstrap(task_id: str, round_no: int) -> str:
    """The command the tab runs: this same engine, in its scout role, on this round."""
    root = str(Path(__file__).resolve().parents[1])
    return (
        "import sys; sys.path.insert(0, {root!r}); "
        "from nexgen_local.supervision_scout import run_in_tab; "
        "raise SystemExit(run_in_tab({task!r}, {round_no!r}))"
    ).format(root=root, task=task_id, round_no=round_no)
