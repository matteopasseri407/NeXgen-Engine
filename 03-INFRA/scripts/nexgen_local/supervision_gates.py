"""The engine's gates on a unit's branch, and the package a supervisor reviews.

Nobody trusts the executor's report: this module reads the commits, runs the
declared verification itself, asks every remote whether anything moved, and
writes one PASS/FAIL line per gate with its reason. The verdict is PASS only
when every line is, and the package carries the diffstat, the diff and the
cost of the attached session.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from nexgen_core.discipline import config, regress
from nexgen_core.discipline.config import ConfigError
from nexgen_core.discipline.gitutil import GitError, changes as changed_files, diff_text, git
from nexgen_core.discipline.integrity import check_test_integrity
from nexgen_core.discipline.violations import TEST_PATH
from nexgen_core.processes import no_window_kwargs, split_command_line, windows_command_argv

from . import supervision_costs as costs
from . import supervision_state as state
from .supervision_state import SupervisionError
from .supervision_worktree import remote_refs

#: The declared budget for the executor's own verification command.
VERIFY_TIMEOUT_S = 600.0
#: The budget for re-running the touched test files to see what this platform skips.
SKIP_TIMEOUT_S = 300.0
#: The package carries at most this much diff; the cut is declared in the file.
DIFF_MAX_LINES = 400
#: Published identity a unit may never carry, unless its own scope says otherwise.
FORBIDDEN_FILES = ("VERSION", "CHANGELOG.md")
FORBIDDEN_PREFIXES = (".github/",)
_SKIP_LINE = re.compile(r"^SKIPPED \[\d+\] (.+?):(\d+): (.*)$")
_SKIPPED_TOTAL = re.compile(r"(\d+) skipped")


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict: a name, PASS/FAIL, one line of reason, and raw detail."""

    name: str
    ok: bool
    reason: str
    detail: str = ""


def collect(task_id: str, unit: str) -> dict:
    """Judge the unit's branch with the engine's gates and write what a reviewer reads."""
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    folder = Path(record.get("worktree") or "")
    if not folder.is_dir():
        raise SupervisionError(f"copia dell'unità sparita: {folder}")
    base, branch = str(record.get("base") or ""), str(record.get("branch") or "")
    if not base or not branch:
        raise SupervisionError(f"unità {name} senza base o ramo nel diario: rifai `sup unit`")
    scope = [str(pattern) for pattern in record.get("allow") or []]
    head = _head(folder, branch)
    try:
        files = changed_files(folder, base, head)
        rules = config.load(folder, base)
    except (GitError, ConfigError) as exc:
        raise SupervisionError(f"cancelli non eseguibili su {folder}: {exc}") from exc
    clean = _clean_tree_gate(folder)
    remote = _remote_gate(folder, record.get("remotes") or {})
    text = diff_text(folder, base, head)
    results = [
        _scope_gate(files, scope),
        _forbidden_gate(files, scope),
        _integrity_gate(files, rules),
        _per_commit_gate(folder, rules, base, head),
        _verify_gate(folder, str(record.get("verify") or "")),
        remote,
        clean,
    ]
    verdict = "PASS" if all(result.ok for result in results) else "FAIL"
    skipped = skipped_tests(folder, files)
    package = _package_text(task_id, name, branch, base, head, verdict, results, folder, text, skipped)
    package_path = state.open_task(task_id) / f"40-pacchetto-{name}.md"
    raw_path = state.open_task(task_id) / f"50-gate-{name}.txt"
    package_path.write_text(package, encoding="utf-8")
    raw_path.write_text(_raw_text(results) + _skipped_raw(skipped), encoding="utf-8")
    state.append_event(
        task_id, "unit_collected",
        unit=name, verdict=verdict, head=head,
        gates=[{"name": result.name, "ok": result.ok, "reason": result.reason} for result in results],
        skipped_tests=skipped,
    )
    return {
        "unit": name, "verdict": verdict, "base": base, "head": head,
        "gates": results, "package": package_path, "raw": raw_path, "skipped": skipped,
    }


def _head(folder: Path, branch: str) -> str:
    """The commit the unit's branch points at now."""
    sha = git(folder, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).strip()
    if not sha:
        raise SupervisionError(f"ramo dell'unità sparito: {branch}")
    return sha


def _allowed(path: str, scope: list[str]) -> bool:
    """A path inside the declared boundary; the globs are the shell's own fnmatch."""
    return any(fnmatch(path, pattern) for pattern in scope)


def _touched(change) -> list[str]:
    """Both names of a rename: moving a file out of the boundary is still a change there."""
    return [change.path] + ([change.old_path] if change.old_path else [])


def _scope_gate(files: list, scope: list[str]) -> GateResult:
    """Every touched file inside the unit's declared boundary."""
    outside = sorted({path for change in files for path in _touched(change) if not _allowed(path, scope)})
    if outside:
        return GateResult("confine", False, "fuori dal confine: " + ", ".join(outside))
    return GateResult("confine", True, f"{len(files)} file toccati, tutti dentro il confine")


def _forbidden_gate(files: list, scope: list[str]) -> GateResult:
    """Version, changelog and CI may move only when the boundary says so out loud."""
    hit = sorted({
        change.path for change in files
        if _is_forbidden(change.path) and not _allowed(change.path, scope)
    })
    if hit:
        return GateResult("file vietati", False, "toccati fuori dal confine: " + ", ".join(hit))
    return GateResult("file vietati", True, "VERSION, CHANGELOG.md e .github/ intatti")


def _is_forbidden(path: str) -> bool:
    return path in FORBIDDEN_FILES or any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


def _integrity_gate(files: list, rules: config.Config) -> GateResult:
    """The engine's own test-integrity check: no existing test deleted, shrunk or skipped."""
    problems = check_test_integrity(files, rules)
    if problems:
        return GateResult("integrità dei test", False, " | ".join(str(problem) for problem in problems))
    return GateResult("integrità dei test", True, "nessun test cancellato, ridotto o saltato")


def _per_commit_gate(folder: Path, rules: config.Config, base: str, head: str) -> GateResult:
    """The discipline's per-commit proof, reused rather than rewritten."""
    try:
        problems = regress.check_commits(folder, rules, base, head)
    except (GitError, OSError) as exc:
        return GateResult("prova per commit", False, f"non verificabile ({type(exc).__name__}: {exc})")
    if problems:
        return GateResult(
            "prova per commit", False,
            " | ".join(str(problem) for problem in problems)[:800],
        )
    return GateResult("prova per commit", True, "ogni commit fix porta un test che fallisce prima")


def _clean_tree_gate(folder: Path) -> GateResult:
    """Uncommitted work is work the unit did not deliver."""
    dirty = git(folder, "status", "--porcelain", check=False).rstrip("\r\n")
    if not dirty:
        return GateResult("albero pulito", True, "nessuna modifica non committata")
    listed = ", ".join(_porcelain_path(line) for line in dirty.splitlines()[:10])
    return GateResult("albero pulito", False, f"modifiche non committate: {listed}", dirty)


def _porcelain_path(line: str) -> str:
    """The path of a `git status --porcelain` row, two status columns and a space first."""
    return line[3:] if len(line) > 3 else line.strip()


def _remote_gate(folder: Path, before: dict) -> GateResult:
    """The refs the unit's base was taken from, compared with the refs now."""
    now = remote_refs(folder)
    if not now:
        return GateResult("remoto intatto", True, "nessun remoto configurato")
    unreadable = [f"{name}: {refs['error']}" for name, refs in now.items() if refs.get("error")]
    if unreadable:
        return GateResult("remoto intatto", False, "non verificabile, remoto non leggibile: " + "; ".join(unreadable))
    if before == now:
        return GateResult("remoto intatto", True, f"{len(now)} remoti, refs identici alla creazione")
    moved = sorted(name for name in set(before) | set(now) if before.get(name) != now.get(name))
    return GateResult("remoto intatto", False, "refs cambiati su: " + ", ".join(moved))


def _verify_gate(folder: Path, command: str) -> GateResult:
    """The executor's declared command, run by the engine with its own launch rules."""
    if not command.strip():
        return GateResult(
            "verifica indipendente", False,
            "nessun comando di verifica dichiarato: senza, il motore non può dire verificato",
        )
    argv = windows_command_argv(split_command_line(command))
    try:
        done = subprocess.run(
            argv, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=VERIFY_TIMEOUT_S, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return GateResult("verifica indipendente", False, f"tempo massimo superato ({VERIFY_TIMEOUT_S:.0f}s)")
    except OSError as exc:
        return GateResult("verifica indipendente", False, f"comando non avviato ({type(exc).__name__}: {exc})")
    output = (done.stdout + done.stderr).strip()
    if done.returncode:
        return GateResult(
            "verifica indipendente", False,
            f"`{command}` esce con {done.returncode}: {_last_line(output)}", output,
        )
    return GateResult("verifica indipendente", True, f"`{command}` esce con 0", output)


def skipped_tests(folder: Path, files: list, *, timeout: float = SKIP_TIMEOUT_S) -> list[str]:
    """Tests this platform skipped among the test files the unit touched, with their reasons.

    A test skipped on Windows runs on the Linux CI, and the other way round: a
    POSIX-only bug of the lazy-mcp split passed here exactly this way. The
    package names the skipped tests so a reviewer knows what was not verified.
    """
    touched = sorted({
        change.path for change in files
        if change.status != "D" and TEST_PATH.search(change.path) and change.path.endswith(".py")
    })
    if not touched:
        return []
    argv = [sys.executable, "-m", "pytest", "-q", "-rs", *touched]
    try:
        done = subprocess.run(
            argv, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout, check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # The list of skipped tests is a report, not a gate: say why it is missing.
        return [f"non raccolti: pytest non ha risposto ({type(exc).__name__})"]
    entries = []
    for line in (done.stdout + done.stderr).splitlines():
        match = _SKIP_LINE.match(line.strip())
        if match:
            entries.append(f"{match.group(1).replace(chr(92), '/')}:{match.group(2)} ({match.group(3)})")
    if entries:
        return entries
    total = _SKIPPED_TOTAL.search(done.stdout)
    if total and int(total.group(1)):
        return [f"{total.group(1)} test saltati, motivi non riportati dal runner"]
    return []


def skipped_label() -> str:
    """What "not verified here" means for the platform the engine is running on."""
    if os.name == "nt":
        return "non verificato su Linux: lo deve dire la CI Linux"
    return "non verificato su questa piattaforma: lo devono dire le altre CI"


def _skipped_section(skipped: list[str]) -> list[str]:
    """The package section, empty-looking when nothing was skipped."""
    if not skipped:
        return ["## Test saltati su questa piattaforma", "", "nessun test saltato fra i file toccati.", ""]
    lines = ["## Test saltati su questa piattaforma", ""]
    lines += [f"- {entry}" for entry in skipped]
    lines += ["", skipped_label() + ".", ""]
    return lines


def _skipped_raw(skipped: list[str]) -> str:
    """The same list in the raw file, so a failure and its skips sit together."""
    if not skipped:
        return ""
    return "\n\nSKIPPED su questa piattaforma:\n" + "\n".join(f"- {entry}" for entry in skipped) + "\n"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:200]


def _raw_text(results: list[GateResult]) -> str:
    """Every gate with its detail, for whoever has to read the failure in full."""
    blocks = []
    for result in results:
        header = f"{'PASS' if result.ok else 'FAIL'} {result.name}: {result.reason}"
        blocks.append(header + (f"\n\n{result.detail}" if result.detail else ""))
    return "\n\n".join(blocks) + "\n"


def _package_text(
    task_id: str, unit: str, branch: str, base: str, head: str, verdict: str,
    results: list[GateResult], folder: Path, text: str, skipped: list[str],
) -> str:
    """The file a supervisor reads: verdict, gate lines, skips, diffstat, diff, cost."""
    kept, cut = _truncated(text, DIFF_MAX_LINES)
    rows = state.read_costs(task_id)
    lines = [
        f"# Pacchetto di revisione: {unit}",
        "",
        f"- compito: {task_id}",
        f"- ramo: {branch}",
        f"- base: {base[:12]}",
        f"- head: {head[:12]}",
        f"- verdetto: {verdict}",
        f"- costo: {_cost_line(rows, unit)}",
        "",
        "## Cancelli",
        "",
        "| cancello | esito | motivo |",
        "|---|---|---|",
        *[f"| {r.name} | {'PASS' if r.ok else 'FAIL'} | {_cell(r.reason)} |" for r in results],
        "",
        *_skipped_section(skipped),
        "## Diffstat",
        "",
        "```",
        git(folder, "diff", "--stat", base, head).strip(),
        "```",
        "",
        "## Diff",
        "",
        "```diff",
        kept,
        "```",
        cut,
        "",
    ]
    return "\n".join(lines)


def _truncated(text: str, limit: int) -> tuple[str, str]:
    """The diff up to `limit` lines, with the cut declared instead of silent."""
    lines = text.splitlines()
    if len(lines) <= limit:
        return text, ""
    return "\n".join(lines[:limit]), (
        f"\nDiff troncato: {len(lines) - limit} righe in più su {len(lines)} "
        f"(tetto dichiarato: {limit} righe). Il diff completo è in `git diff`."
    )


def _cell(text: str) -> str:
    """One table cell: the reason keeps one line and no pipe breaks the row."""
    return text.replace("|", "/").replace("\n", " ")[:300]


def _cost_line(rows: list[dict], unit: str) -> str:
    """What the unit cost, measured when it is collected: the number a reviewer needs.

    The attach wrote its row when the session had just started, so its cost is
    the cost of nothing. The register keeps that event; the package asks the
    harness again, and when it cannot answer it says so instead of showing zero.
    """
    mine = [row for row in rows if row.get("unit") == unit]
    if not mine:
        return "nessuna sessione agganciata"
    session = str(mine[-1].get("session") or "")
    if not session:
        return "nessuna sessione agganciata"
    try:
        facts = session_facts(session)
    except costs.CostError as exc:
        return f"non misurato alla raccolta ({exc}) · sessione {session}"
    cost = facts.cost_usd
    money = f"{cost:.4f} $" if isinstance(cost, (int, float)) else "costo non disponibile"
    duration = f"{facts.duration_s:.1f}s · " if facts.duration_s is not None else ""
    children = ""
    if facts.children:
        children = f" · {facts.children} figlie ({facts.children_cost_usd or 0:.4f} $)"
    elif facts.children is None and facts.children_source:
        children = " · figlie non esposte"
    return (
        f"{money} ({_at_collection(rows, unit, cost)}) · sessione {session} · {duration}"
        f"{facts.tokens_in or 0} token in, {facts.tokens_out or 0} token out{children}"
    )


def session_facts(session_id: str) -> costs.SessionFacts:
    """The session as the harness reports it now, children included; a broken source raises CostError."""
    return costs.measure(session_id)


def _at_collection(rows: list[dict], unit: str, cost: float | None) -> str:
    """Say whether the number grew since the attach, so nobody reads a stale figure."""
    attached = [row.get("cost_usd") for row in rows if row.get("unit") == unit]
    first = next((value for value in attached if isinstance(value, (int, float))), None)
    if first is None or cost is None or first == cost:
        return "alla raccolta"
    return f"alla raccolta, {first:.4f} $ all'aggancio"
