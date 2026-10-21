"""The engine's gates on a unit's branch, and the package a supervisor reviews.

Nobody trusts the executor's report: this module reads the commits, runs the
declared verification itself, asks every remote whether anything moved, and
writes one PASS/FAIL line per gate with its reason. The verdict is PASS only
when every line is, and the package carries the diffstat, the diff and the
cost of the attached session. Collecting also writes down the requests the
engine will never execute, so the owner finds them in `07-richieste-proprietario.md`.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
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
from . import supervision_gitconfig as gitconfig
from . import supervision_policy as policy
from . import supervision_pytest as pytest_runs
from . import supervision_requests as requests_module
from . import supervision_state as state
from .supervision_state import SupervisionError
from .supervision_package import DIFF_MAX_LINES, package_text, raw_text  # noqa: F401  DIFF_MAX_LINES: re-exported, the gate tests read it here
from .supervision_worktree import BLOCKED_PUSH_URL, push_urls, refs_at, remote_urls

#: The declared budget for the executor's own verification command.
VERIFY_TIMEOUT_S = 600.0
#: Shell operators the verification command may not carry: it is one command, not a script.
SHELL_OPERATORS = ("&&", "||", "|", ";", "&")
#: Suffixes Windows tries for a bare name, in the order the loader tries them.
WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
#: A Python interpreter by name; its `-m` would put the working directory on `sys.path`.
PYTHON_EXECUTABLE = re.compile(r"^python(\d+(\.\d+)*)?(\.exe)?$", re.IGNORECASE)
PYTHON_LAUNCHER = re.compile(r"^py(\.exe)?$", re.IGNORECASE)
#: The budget for re-running the touched test files to see what this platform skips.
SKIP_TIMEOUT_S = 300.0
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
    head = head_of(folder, branch)
    try:
        files = changed_files(folder, base, head)
        rules = config.load(folder, base)
    except (GitError, ConfigError) as exc:
        raise SupervisionError(f"cancelli non eseguibili su {folder}: {exc}") from exc
    clean = _clean_tree_gate(folder)
    remote = _remote_gate(folder, record, base, head)
    text = diff_text(folder, base, head)
    surface = pytest_runs.surface(files)
    results = [
        _scope_gate(files, scope),
        _forbidden_gate(files, scope),
        _integrity_gate(files, rules),
        _per_commit_gate(folder, rules, base, head),
        _verify_gate(folder, str(record.get("verify") or "")),
        _collected_tests_gate(folder, base, head, files, surface),
        _model_gate(record),
        _git_config_gate(record, folder),
        remote,
        clean,
    ]
    verdict = "PASS" if all(result.ok for result in results) else "FAIL"
    skipped = skipped_tests(folder, files)
    request_scan = requests_module.scan_report(task_id, name)
    package = package_text(
        task_id=task_id, unit=name, branch=branch, base=base, head=head, verdict=verdict,
        results=results, cost_line=_cost_line(state.read_costs(task_id), name),
        diffstat=git(folder, "diff", "--stat", base, head).strip(), diff=text,
        skipped=skipped, request_scan=request_scan, pytest_surface=surface,
    )
    package_path = state.open_task(task_id) / f"40-pacchetto-{name}.md"
    raw_path = state.open_task(task_id) / f"50-gate-{name}.txt"
    package_path.write_text(package, encoding="utf-8")
    raw_path.write_text(raw_text(results, skipped), encoding="utf-8")
    state.append_event(
        task_id, "unit_collected",
        unit=name, verdict=verdict, head=head,
        gates=[{"name": result.name, "ok": result.ok, "reason": result.reason} for result in results],
        skipped_tests=skipped,
        requests=[hit["kind"] for hit in request_scan["requests"]],
        request_section=request_scan["found"],
    )
    return {
        "unit": name, "verdict": verdict, "base": base, "head": head,
        "gates": results, "package": package_path, "raw": raw_path, "skipped": skipped,
        "requests": request_scan["requests"],
        "request_section": requests_module.section_status(request_scan),
    }


def head_of(folder: Path, branch: str) -> str:
    """The commit the unit's branch points at now; a vanished branch is an error."""
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
    """Version, changelog and CI may move only when the boundary says so out loud.

    Both names of a rename count: `git mv VERSION altro` is a change to `VERSION`
    as much as deleting it, and looking only at the new name would hide it.
    """
    hit = sorted({
        path for change in files for path in _touched(change)
        if _is_forbidden(path) and not _allowed(path, scope)
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


def _model_gate(record: dict) -> GateResult:
    """The session's model is on the owner's list, when there is one."""
    ok, reason = policy.check_session(record, policy.allowed_models())
    return GateResult("modello consentito", ok, reason)


def _git_config_gate(record: dict, folder: Path) -> GateResult:
    """The copy's git configuration is the creation's own, and carries no URL rewrite added since.

    `git config` run from a worktree without `--worktree` writes the owner's shared
    `.git/config`, which every checkout of the repository reads. The two files are
    compared by hash, so the one that changed is named, and a `url.*.insteadOf`
    rule is refused separately because it re-points a push while every URL the copy
    reports stays exactly the same.
    """
    ok, reason = gitconfig.check(folder, record)
    return GateResult("configurazione git", ok, reason)


def _remote_gate(folder: Path, record: dict, base: str, head: str) -> GateResult:
    """The remotes of the creation, and whether any of their refs carries a commit of this unit.

    A remote that disappeared, changed its fetch URL, changed its push URL or
    was added after creation is a FAIL, because that is how a push is hidden:
    `remote.<name>.pushurl` wins over the fetch URL for every push, and a new
    remote is a new way out of the copy. A remote branch that advanced for
    another reason (another unit was published) is not a failure: the question
    is whether *this unit's* commits reached a remote, and it is asked of the
    URLs written at creation, which a unit cannot edit without being seen.
    """
    problem, saved = _remote_config_gate(record, folder)
    if problem is not None:
        return problem
    if not saved:
        return GateResult("remoto intatto", True, "nessun remoto configurato alla creazione")
    mine = set(git(folder, "rev-list", f"{base}..{head}").split())
    pushed: list[str] = []
    unreadable: list[str] = []
    for name in sorted(saved):
        refs = refs_at(saved[name])
        if "error" in refs:
            unreadable.append(f"{name}: {refs['error']}")
            continue
        pushed += [
            f"{name}/{ref.removeprefix('refs/heads/')} → {sha[:12]}"
            for ref, sha in refs.items() if sha in mine
        ]
    if unreadable:
        return GateResult("remoto intatto", False, "non verificabile, remoto non leggibile: " + "; ".join(unreadable))
    if pushed:
        return GateResult(
            "remoto intatto", False,
            "l'unità ha spinto: " + ", ".join(sorted(pushed)),
        )
    return GateResult(
        "remoto intatto", True,
        f"{len(saved)} remoti, nessun ref remoto porta un commit dell'unità",
    )


def _remote_config_gate(record: dict, folder: Path) -> tuple[GateResult | None, dict]:
    """The remote configuration is the creation's own, and returns the URLs to ask.

    A remote gone, added, re-pointed for fetch or for push is a FAIL. The
    returned dict is the creation's fetch URLs, or today's when the journal
    predates the field: the refs are then asked of the only URLs there are.
    """
    saved = {str(name): str(url) for name, url in (record.get("remote_urls") or {}).items()}
    saved_push = {str(name): str(url) for name, url in (record.get("remote_pushurls") or {}).items()}
    now = remote_urls(folder)
    now_push = push_urls(folder)
    if not saved:
        return None, now
    missing = sorted(name for name in saved if not now.get(name))
    if missing:
        return GateResult("remoto intatto", False, "remoti spariti dalla copia: " + ", ".join(missing)), saved
    added = sorted(name for name in now if name not in saved)
    if added:
        return GateResult("remoto intatto", False, "remoti aggiunti alla copia: " + ", ".join(added)), saved
    changed = sorted(name for name in saved if now.get(name) and now[name] != saved[name])
    if changed:
        detail = ", ".join(f"{name} ({saved[name]} → {now[name]})" for name in changed)
        return GateResult("remoto intatto", False, "URL di fetch cambiato: " + detail), saved
    moved = sorted(name for name in saved if now_push.get(name, "") != saved_push.get(name, BLOCKED_PUSH_URL))
    if moved:
        detail = ", ".join(
            f"{name} ({saved_push.get(name, BLOCKED_PUSH_URL)} → {now_push.get(name, '')})" for name in moved
        )
        return GateResult("remoto intatto", False, "pushurl cambiata: " + detail), saved
    return None, saved


def _verify_gate(folder: Path, command: str) -> GateResult:
    """The executor's declared command, run by the engine with its own launch rules.

    Two things are refused before anything runs. The command is one command, not
    a shell line: `&&`, `||`, `|`, `;` and `&` are not supported, and a verifier
    that needs several steps writes a script. And the executable must not come
    from the unit's own copy: Windows looks in the working directory before PATH,
    so a `pytest.bat` dropped in the copy would answer for the real runner. A
    Python interpreter is started with `-P`: without it `python -m pytest` puts
    the working directory first on `sys.path` and imports the copy's own
    `pytest.py`, which is the same bypass by another name. `-P` does not touch
    `PYTHONPATH`, so the environment is cleaned too: the copy decides nothing
    about what the interpreter imports.
    """
    if not command.strip():
        return GateResult(
            "verifica indipendente", False,
            "nessun comando di verifica dichiarato: senza, il motore non può dire verificato",
        )
    argv = split_command_line(command)
    shell = [token for token in argv if token in SHELL_OPERATORS]
    if shell:
        return GateResult(
            "verifica indipendente", False,
            f"il comando di verifica contiene {', '.join(shell)}: non passa da una shell. "
            "Scrivi uno script o un solo comando",
        )
    inside = _executable_inside(argv, folder)
    if inside is not None:
        return GateResult(
            "verifica indipendente", False,
            f"l'eseguibile `{argv[0]}` della verifica sta dentro la copia dell'unità ({inside}): "
            "un esecutore non può fornire il proprio verificatore. Usa un interprete o un runner fuori dalla copia",
        )
    try:
        done = subprocess.run(
            windows_command_argv(_launch_argv(argv)), cwd=folder, capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=pytest_runs.cleaned_environment(),
            timeout=VERIFY_TIMEOUT_S, check=False, **no_window_kwargs(),
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


def _collected_tests_gate(folder: Path, base: str, head: str, files: list, surface: list[str]) -> GateResult:
    """How many tests pytest collects now against the base: an AST count cannot see `__test__ = False`.

    The count is taken in two exported copies of the interval, never in the unit's
    own one, and both numbers are in the reason, so a FAIL says what was lost.
    """
    ok, reason = pytest_runs.check(folder, base, head, files, surface)
    return GateResult("test raccolti", ok, reason)


def _launch_argv(argv: list[str]) -> list[str]:
    """The verification argv, with `-P` when it starts a Python interpreter.

    Python 3.11 answers `-P` with "do not prepend the working directory to
    `sys.path`". The engine's own Python requirement is 3.11, so the flag is
    always available where the engine runs.
    """
    if not argv:
        return []
    name = Path(argv[0]).name
    if PYTHON_EXECUTABLE.match(name) or (os.name == "nt" and PYTHON_LAUNCHER.match(name)):
        return [argv[0], "-P", *argv[1:]]
    return list(argv)


def _executable_inside(argv: list[str], folder: Path) -> Path | None:
    """The file the engine would run when the unit's own copy provides it, or None."""
    if not argv:
        return None
    name = argv[0]
    candidate = Path(name)
    root = folder.resolve()
    if candidate.is_absolute() or len(candidate.parts) > 1:
        resolved = candidate if candidate.is_absolute() else (folder / candidate)
        return resolved if _within(resolved, root) else None
    local = [folder / name]
    if os.name == "nt":
        local += [folder / f"{name}{suffix}" for suffix in WINDOWS_EXECUTABLE_SUFFIXES]
    for path in local:
        if path.is_file():
            return path
    found = shutil.which(name)
    return Path(found) if found and _within(Path(found), root) else None


def _within(path: Path, root: Path) -> bool:
    """True when `path` is inside `root`, with a different drive counting as outside."""
    return Path(path).resolve().is_relative_to(root)


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
    argv = [sys.executable, "-m", "pytest", "-q", "-rs", *_worker_args(), *touched]
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


def _worker_args() -> list[str]:
    """`-n 4` when pytest-xdist is installed: the same suite, four workers, inside the same budget."""
    return ["-n", "4"] if importlib.util.find_spec("xdist") is not None else []


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:200]


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
