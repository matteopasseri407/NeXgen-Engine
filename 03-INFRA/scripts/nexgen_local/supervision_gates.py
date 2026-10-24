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
from nexgen_core.processes import no_window_kwargs

from . import supervision_costs as costs
from . import supervision_gitconfig as gitconfig
from . import supervision_lint as lint
from . import supervision_policy as policy
from . import supervision_pytest as pytest_runs
from . import supervision_remote as remotes
from . import supervision_requests as requests_module
from . import supervision_retirement as own_rules
from . import supervision_state as state
from . import supervision_verify as verify
from .supervision_state import SupervisionError
from .supervision_package import DIFF_MAX_LINES, package_text, raw_text  # noqa: F401  DIFF_MAX_LINES: re-exported, the gate tests read it here

#: The budget for re-running the touched test files to see what this platform skips.
SKIP_TIMEOUT_S = 300.0
#: The project's own anti-leak scan: the workflow names it, these two files run it.
LEAK_WORKFLOW = "leak_scan.py"
LEAK_SCRIPT = "03-INFRA/agent-universal-layer/leak-scan/leak_scan.py"
LEAK_PATTERNS = "03-INFRA/agent-universal-layer/leak-scan/leak_patterns.yaml"
LEAK_TIMEOUT_S = 300.0
#: The report an executor sometimes leaves inside the copy instead of the task folder.
REPORT_FILE = "30-resoconto-{unit}.md"
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
    folder = _copy_of(record)
    base, branch = str(record.get("base") or ""), str(record.get("branch") or "")
    if not base or not branch:
        raise SupervisionError(f"unità {name} senza base o ramo nel diario: rifai `sup unit`")
    scope = [str(pattern) for pattern in record.get("allow") or []]
    moved = relocate_report(task_id, name, folder)
    head = head_of(folder, branch)
    try:
        files = changed_files(folder, base, head)
        rules = config.load(folder, base)
    except (GitError, ConfigError) as exc:
        raise SupervisionError(f"cancelli non eseguibili su {folder}: {exc}") from exc
    text = diff_text(folder, base, head)
    surface = pytest_runs.surface(files)
    results = _gate_results(folder, record, base, head, files, rules, scope, surface)
    verdict = "PASS" if all(result.ok for result in results) else "FAIL"
    skipped = skipped_tests(folder, files)
    request_scan = requests_module.scan_report(task_id, name)
    added, unreadable = own_rules.added_entries(folder, head, rules)
    harness = str(record.get("harness") or "opencode")
    price = costs.collect_cost(task_id, name, facts_of=facts_for(harness), harness=harness)
    package = package_text(
        task_id=task_id, unit=name, branch=branch, base=base, head=head, verdict=verdict,
        results=results, cost_line=costs.cost_text(price),
        diffstat=git(folder, "diff", "--stat", base, head).strip(), diff=text,
        skipped=skipped, request_scan=request_scan, pytest_surface=surface,
        test_removals=added, test_removals_error=unreadable,
    )
    package_path = state.open_task(task_id) / f"40-pacchetto-{name}.md"
    raw_path = state.open_task(task_id) / f"50-gate-{name}.txt"
    package_path.write_text(package, encoding="utf-8")
    raw_path.write_text(raw_text(results, skipped), encoding="utf-8")
    _record_collection(task_id, name, verdict=verdict, head=head, results=results,
                       skipped=skipped, request_scan=request_scan, price=price)
    local_notes = _release_local(task_id, name, record)
    return {
        "unit": name, "verdict": verdict, "base": base, "head": head,
        "gates": results, "package": package_path, "raw": raw_path, "skipped": skipped,
        "requests": request_scan["requests"],
        "request_section": requests_module.section_status(request_scan),
        "cost": price, "report_note": moved, "local_notes": local_notes,
    }


def _gate_results(
    folder: Path, record: dict, base: str, head: str, files: list,
    rules: config.Config, scope: list[str], surface: list[str],
) -> list[GateResult]:
    """Every gate on the unit's interval, in the order the package prints them.

    The clean tree and the remotes are read before anything runs in the copy:
    the verification is a command of the unit's own and may leave files behind.
    """
    clean = _clean_tree_gate(folder)
    remote = GateResult("remoto intatto", *remotes.check(folder, record, base, head))
    results = [
        _scope_gate(files, scope),
        _forbidden_gate(files, scope),
        _integrity_gate(folder, base, head, files, rules, scope),
        _per_commit_gate(folder, rules, base, head),
        _verify_gate(folder, str(record.get("verify") or "")),
        _collected_tests_gate(folder, base, head, files, surface, scope),
        _lint_gate(folder),
        _model_gate(record),
        _git_config_gate(record, folder),
        remote,
        clean,
    ]
    return results + _leak_gate(folder, base, head)


def _release_local(task_id: str, unit: str, record: dict) -> list[str]:
    """Collection is the end of a local unit's work, so its server goes when nobody else uses it."""
    from . import supervision_local_model as local  # noqa: PLC0415  the lifecycle is only needed here

    return local.release_for_unit(task_id, unit, record)


def _record_collection(
    task_id: str, name: str, *, verdict: str, head: str, results: list,
    skipped: list, request_scan: dict, price: costs.CollectCost,
) -> None:
    """The journal row of a collection: the verdict, what was skipped, and what it cost."""
    state.append_event(
        task_id, "unit_collected",
        unit=name, verdict=verdict, head=head,
        gates=[{"name": result.name, "ok": result.ok, "reason": result.reason} for result in results],
        skipped_tests=skipped,
        requests=[hit["kind"] for hit in request_scan["requests"]],
        request_section=request_scan["found"],
        cost=price.as_dict(),
    )


def _copy_of(record: dict) -> Path:
    """The unit's working copy as the journal names it; a vanished one is an error."""
    folder = Path(record.get("worktree") or "")
    if not folder.is_dir():
        raise SupervisionError(f"copia dell'unità sparita: {folder}")
    return folder


def relocate_report(task_id: str, unit: str, folder: Path) -> str:
    """Move a report the executor left inside the copy to the task folder, and say so.

    The local executor wrote its report in the working copy, where it became an
    untracked file that failed the clean-tree gate and never reached the reviewer.
    Only an untracked file is moved: a tracked one is part of the unit's own diff,
    and moving it would hide a change the gates must judge.
    """
    name = REPORT_FILE.format(unit=unit)
    source = Path(folder) / name
    if not source.is_file():
        return ""
    if not git(folder, "status", "--porcelain", "--", name, check=False).startswith("??"):
        return f"{name} è tracciato nella copia: resta lì, lo giudicano i cancelli del diff"
    target = state.open_task(task_id) / name
    if target.is_file() and target.read_text(encoding="utf-8") != source.read_text(encoding="utf-8"):
        target = state.open_task(task_id) / f"30-resoconto-{unit}-dalla-copia.md"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    source.unlink()
    state.append_event(task_id, "report_relocated", unit=unit, source=str(source), target=str(target))
    return f"resoconto trovato nella copia: spostato in {target}"


def _leak_gate(folder: Path, base: str, head: str) -> list[GateResult]:
    """Run the project's own anti-leak scan on the unit's range, when the project declares one.

    The scan is the same script CI runs, on the same interval: a leak then fails
    at collection, where the executor can fix it, instead of at the push. A
    project that declares no scan adds no line, so the gate list is what it was.
    """
    declared = _declares_leak_scan(folder)
    if not declared:
        return []
    script, patterns = Path(folder) / LEAK_SCRIPT, Path(folder) / LEAK_PATTERNS
    if not script.is_file() or not patterns.is_file():
        return [GateResult("scansione fughe", False, f"dichiarata ma {LEAK_SCRIPT} o {LEAK_PATTERNS} mancano")]
    argv = [
        sys.executable, str(script), "--patterns", str(patterns),
        "--repo", str(folder), "--commit-range", f"{base}..{head}",
    ]
    try:
        done = subprocess.run(
            argv, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=LEAK_TIMEOUT_S, check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [GateResult("scansione fughe", False, f"non eseguibile ({type(exc).__name__})")]
    if done.returncode == 0:
        return [GateResult("scansione fughe", True, "nessun dato personale nei commit dell'unità")]
    detail = " | ".join(
        line.strip() for line in (done.stdout + done.stderr).splitlines() if line.strip()
    )[:800]
    return [GateResult("scansione fughe", False, detail or f"la scansione esce con {done.returncode}")]


def _declares_leak_scan(folder: Path) -> bool:
    """Whether this project's workflows name the anti-leak scan, which is how it declares it."""
    workflows = Path(folder) / ".github" / "workflows"
    if not workflows.is_dir():
        return False
    for path in workflows.glob("*.y*ml"):
        try:
            if LEAK_WORKFLOW in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:  # nexgen: allow reason="un workflow illeggibile non dichiara niente"
            continue
    return False


def facts_for(harness: str):
    """The cost reader of this unit's harness, with OpenCode keeping its tested seam.

    `gates.session_facts` is patched by the tests and used by the engine for the
    harness the engine grew up with; every other harness asks its own adapter, and
    a refusal becomes the same missing number the package already knows how to say.
    """
    name = (harness or "opencode").strip() or "opencode"
    if name in ("opencode", "local"):
        return session_facts
    return lambda session: costs.measure_for(name, session)


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


def _integrity_gate(
    folder: Path, base: str, head: str, files: list, rules: config.Config, scope: list[str],
) -> GateResult:
    """The engine's own test-integrity check: no existing test deleted, shrunk or skipped.

    A unit whose boundary holds `.nexgen-discipline.toml` is judged the way the
    project's hook judges it, commit by commit against the rules each commit
    inherited: it may carry the authorisations, and may not grant itself one in
    the same commit that uses it. Every other unit is judged over the whole
    interval against the base's rules, exactly as before.
    """
    if own_rules.may_carry(scope):
        problems, retired = own_rules.integrity_by_commit(folder, base, head)
        if problems:
            return GateResult("integrità dei test", False, " | ".join(problems)[:800])
        return GateResult("integrità dei test", True, own_rules.retirement_reason(retired))
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


def _verify_gate(folder: Path, command: str) -> GateResult:
    """The executor's declared command, run by the engine with its own launch rules.

    The launch rules themselves live in `supervision_verify`; this is only the
    gate's name and its place in the verdict.
    """
    return GateResult("verifica indipendente", *verify.run(folder, command))


def _collected_tests_gate(
    folder: Path, base: str, head: str, files: list, surface: list[str], scope: list[str],
) -> GateResult:
    """What pytest collects now against the base: an AST count cannot see `__test__ = False`.

    Both trees are exported and collected, never the unit's own copy. A unit the
    supervisor let carry `.nexgen-discipline.toml` is judged commit by commit,
    so only an authorisation that already existed when the test went counts.
    """
    ok, reason = pytest_runs.check(
        folder, base, head, files, surface, by_commit=own_rules.may_carry(scope),
    )
    return GateResult("test raccolti", ok, reason)


def _lint_gate(folder: Path) -> GateResult:
    """Ruff on the copy, against the copy's own baseline: new lint debt fails, old debt is not this gate's job.

    The decision and the run live in `supervision_lint`; here it is only one more
    line in the verdict. `RUFF_CMD` is left alone: it is the lint script's own
    documented test hook, and it comes from the engine's environment, which no
    unit can write.
    """
    ok, reason, detail = lint.run(folder)
    return GateResult("lint", ok, reason, detail)


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


def session_facts(session_id: str) -> costs.SessionFacts:
    """The session as the harness reports it now, children included; a broken source raises CostError."""
    return costs.measure(session_id)
