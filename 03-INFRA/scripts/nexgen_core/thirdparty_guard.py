"""Deterministic third-party update guardian for NeXgen Engine v2.

depwatch reports WHAT moved upstream; this module judges whether the move
is boring enough to fast-forward without a human reading it. No models in
the loop, ever: every rule below is static, auditable, and wrong in the
safe direction (a suspicious update is held, never waved through).

Three verdicts, in order of trust:

- AUTO: provably zero behavior change (the vendored bytes are identical
  between pin and HEAD). Nothing the runtimes load can differ.
- BATCH: safe-shaped and small (prose/docs only, descendant commit,
  patch-only npm). Shown as a one-shot list for a single human yes.
- HOLD: everything else (new scripts, hooks, non-prose files touched,
  rewritten history, minor/major npm jumps). Per-item manual review.

NEVER notifies, NEVER writes pins or manifests: v1 only writes the
machine-readable verdict file next to depwatch's report, for the
notifier lanes and a future batch-apply command to read. Being offline
is not an incident: git/npm failures hold the item, they never fail
the run.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from nexgen_core.depwatch import PinFinding

GIT_TIMEOUT_SECONDS = 120

#: Tiers of trust, weakest first. Anything unlisted is HOLD by construction.
AUTO = "auto"
BATCH = "batch"
HOLD = "hold"

#: A new file with one of these suffixes can run: held, always.
SCRIPT_SUFFIXES = frozenset({
    ".js", ".mjs", ".cjs", ".sh", ".py", ".rb", ".pl", ".php",
    ".ps1", ".bat", ".cmd", ".exe", ".dll", ".so", ".bin",
})

#: Changes limited to these suffixes are prose or pictures: batchable.
PROSE_SUFFIXES = frozenset({
    ".md", ".mdx", ".txt", ".rst",
    ".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp",
})

#: Hook and plugin wiring is never boring: held, always.
HOOK_NAMES = frozenset({"hooks.json", "plugin.json", "settings.json", "hooks.yaml"})

#: A batchable diff stays small enough to glance at in one go.
BATCH_MAX_FILES = 20
BATCH_MAX_LINES = 300

STRICT_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass
class GuardFinding:
    what: str
    verdict: str = HOLD
    reasons: list[str] = field(default_factory=list)
    pinned: str = ""
    upstream: str = ""
    #: One line a non-technical human can say yes or no to.
    plain: str = ""


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def _resolve_clone_target(repo: str) -> str:
    try:
        from nexgen_core.skill_sources import clone_url

        return clone_url(repo)
    except Exception:
        return repo


def _clone_no_checkout(repo: str, dest: Path) -> bool:
    target = _resolve_clone_target(repo)
    res = _run_git(
        ["clone", "--quiet", "--no-checkout", "--filter=blob:none", target, str(dest)],
        cwd=dest.parent,
    )
    if res.returncode == 0:
        return True
    shutil.rmtree(dest, ignore_errors=True)
    res = _run_git(["clone", "--quiet", "--no-checkout", target, str(dest)], cwd=dest.parent)
    return res.returncode == 0


def _in_scope(path: str, scope: str) -> bool:
    return scope in (".", "") or path == scope or path.startswith(scope.rstrip("/") + "/")


def _parse_name_status_z(output: str) -> list[tuple[str, str]]:
    """Parses `git diff --name-status -z`: STATUS NUL PATH NUL records.

    Renames carry an extra path (the new one wins). Anything unreadable
    yields no entries, which the caller reads as HOLD, never AUTO.
    """
    tokens = output.split("\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        flag = tokens[i]
        i += 1
        if not flag or i >= len(tokens):
            break
        name = tokens[i]
        i += 1
        if flag.startswith("R") and i < len(tokens):
            name = tokens[i]
            i += 1
        if name:
            entries.append((flag, name))
    return entries


def judge_github_skill(
    what: str, repo: str, pinned: str, head: str, scope: str = ".",
) -> GuardFinding:
    """Verdict for one github-pinned skill, from the diff alone."""
    finding = GuardFinding(what=what, pinned=pinned, upstream=head)
    tmp_parent = Path(tempfile.gettempdir())
    work = tmp_parent / f".nexgen-guard-{time.time_ns()}"

    def _ok(proc: subprocess.CompletedProcess[str], step: str) -> bool:
        if proc.returncode == 0:
            return True
        finding.reasons.append(f"inspection failed at {step}, cannot verify")
        finding.plain = "Non sono riuscita a controllare bene: resta fermo, ci riprovo da sola."
        return False

    try:
        if not _clone_no_checkout(repo, work):
            finding.reasons.append("cannot clone for inspection")
            finding.plain = "Non sono riuscita a scaricarlo per controllarlo: ci riprovo da sola al prossimo giro."
            return finding
        for rev in (pinned, head):
            probe = _run_git(["rev-parse", "--verify", f"{rev}^{{commit}}"], cwd=work)
            if probe.returncode != 0:
                finding.reasons.append(f"rev {rev[:9]} not reachable, cannot verify")
                finding.plain = "Non torna qualcosa nei numeri di versione: da guardare prima."
                return finding
        ancestor = _run_git(["merge-base", "--is-ancestor", pinned, head], cwd=work)
        if ancestor.returncode != 0:
            finding.reasons.append("HEAD is not a descendant of the pin: history rewritten")
            finding.plain = "La storia del progetto è stata riscritta: strano, da guardare prima."
            return finding

        scope_arg = [] if scope in (".", "") else ["--", scope]
        # -z: NUL-separated paths, never C-quoted. A quoted non-ASCII
        # name ("skills/demo/\303\251vil.py") would otherwise miss the
        # scope check, read as an empty diff, and wrongly verdict AUTO.
        status = _run_git(["diff", "--name-status", "-z", f"{pinned}..{head}", *scope_arg], cwd=work)
        if not _ok(status, "diff"):
            return finding
        entries = _parse_name_status_z(status.stdout)
        scoped = [(flag, name) for flag, name in entries if _in_scope(name, scope)]
        if not scoped:
            finding.verdict = AUTO
            finding.reasons.append("vendored bytes identical between pin and HEAD")
            finding.plain = "Contenuto identico a prima: cambia solo il numero, niente di nuovo dentro."
            return finding

        skill_md = "SKILL.md" if scope in (".", "") else f"{scope.rstrip('/')}/SKILL.md"
        tree = _run_git(["ls-tree", head, skill_md], cwd=work)
        if not _ok(tree, "ls-tree"):
            return finding
        if not tree.stdout.strip():
            finding.reasons.append("SKILL.md missing at HEAD in the vendored scope")
            finding.plain = "Sparisce il file principale della skill: da guardare prima."
            return finding

        added = [name for flag, name in scoped if flag == "A"]
        for name in added:
            if Path(name).suffix.lower() in SCRIPT_SUFFIXES:
                finding.reasons.append(f"new script added: {name}")
                finding.plain = f"C'è un programma nuovo dentro ({name}): da guardare prima."
                return finding
            if Path(name).name in HOOK_NAMES or "/hooks/" in name:
                finding.reasons.append(f"new hook wiring added: {name}")
                finding.plain = f"Tocca i collegamenti automatici ({name}): da guardare prima."
                return finding
            mode = _run_git(["ls-tree", head, "--", name], cwd=work)
            if not _ok(mode, "ls-tree"):
                return finding
            if re.match(r"^\d*755\s", mode.stdout.strip()):
                finding.reasons.append(f"new executable file: {name}")
                finding.plain = f"C'è un file nuovo che può partire da solo ({name}): da guardare prima."
                return finding

        summary = _run_git(["diff", "--summary", f"{pinned}..{head}", *scope_arg], cwd=work)
        if not _ok(summary, "diff"):
            return finding
        if re.search(r"mode change \d+ => \d*755 ", summary.stdout):
            finding.reasons.append("an existing file gained the executable bit")
            finding.plain = "Un file esistente diventa eseguibile: da guardare prima."
            return finding

        touched_hooks = [
            name for _, name in scoped
            if Path(name).name in HOOK_NAMES or "/hooks/" in name
        ]
        if touched_hooks:
            finding.reasons.append(f"hook wiring touched: {', '.join(touched_hooks[:5])}")
            finding.plain = f"Tocca i collegamenti automatici ({', '.join(touched_hooks[:2])}): da guardare prima."
            return finding

        foreign = [name for _, name in scoped if Path(name).suffix.lower() not in PROSE_SUFFIXES]
        if foreign:
            finding.reasons.append(f"non-prose files touched: {', '.join(foreign[:5])}")
            finding.plain = f"Cambia file che non sono testi ({', '.join(foreign[:2])}): da guardare prima."
            return finding

        numstat = _run_git(["diff", "--numstat", f"{pinned}..{head}", *scope_arg], cwd=work)
        if not _ok(numstat, "diff"):
            return finding
        files, lines = 0, 0
        for line in numstat.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                files += 1
                lines += int(parts[0]) + int(parts[1])
        if files > BATCH_MAX_FILES or lines > BATCH_MAX_LINES:
            finding.reasons.append(f"too big to batch blindly ({files} files, {lines} lines)")
            finding.plain = f"Cambia troppa roba in una volta ({files} file): da guardare prima."
            return finding

        finding.verdict = BATCH
        finding.reasons.append(f"prose-only, {files} files, {lines} lines")
        finding.plain = f"Solo testi ritoccati ({files} file): nessun programma nuovo, stesso autore."
        return finding
    except (OSError, subprocess.TimeoutExpired) as exc:
        finding.reasons.append(f"inspection failed: {exc}")
        finding.plain = "Il controllo si è interrotto a metà: resta fermo, ci riprovo da sola."
        return finding
    finally:
        shutil.rmtree(work, ignore_errors=True)


def judge_npm_package(what: str, pinned: str, latest: str) -> GuardFinding:
    """Verdict for one npm pin: patch-only batches, the rest is held.

    Even a patch ships code that runs, so npm never fast-forwards alone:
    the best verdict here is a one-shot list with exact versions. Anything
    that is not a strict patch jump (minor, major, date trains) is held
    for a human to read the changelog first.
    """
    finding = GuardFinding(what=what, pinned=pinned, upstream=latest)
    old, new = STRICT_SEMVER_RE.match(pinned.strip()), STRICT_SEMVER_RE.match(latest.strip())
    if not old or not new:
        finding.reasons.append("not a strict x.y.z version, cannot judge the jump")
        finding.plain = "Versione scritta in modo strano: non so giudicarla, resta ferma."
        return finding
    old_v = tuple(int(g) for g in old.groups())
    new_v = tuple(int(g) for g in new.groups())
    if new_v <= old_v:
        finding.reasons.append("upstream is not newer")
        finding.plain = "Niente di nuovo: resta dov'è."
        return finding
    if new_v[0] != old_v[0]:
        finding.reasons.append(f"major jump {pinned} -> {latest}: read the changelog")
        finding.plain = f"Salto grosso ({pinned} → {latest}): da guardare con calma."
        return finding
    if new_v[1] != old_v[1]:
        finding.reasons.append(f"minor jump {pinned} -> {latest}: read the changelog")
        finding.plain = f"Ci sono novità dentro ({pinned} → {latest}): da guardare prima."
        return finding
    finding.verdict = BATCH
    finding.reasons.append(f"patch jump {pinned} -> {latest}")
    finding.plain = f"Piccolo ritocco ({pinned} → {latest}), stessa linea: niente funzioni nuove."
    return finding


def judge_finding(
    finding: PinFinding, skill_scopes: dict[str, str] | None = None,
) -> GuardFinding:
    """Routes one stale depwatch finding to the right deterministic judge."""
    if not finding.stale or finding.upstream is None:
        verdict = GuardFinding(what=finding.what, pinned=finding.pinned,
                               upstream=finding.upstream or "")
        verdict.reasons.append("nothing moved or unreachable")
        verdict.plain = "Niente di nuovo: resta dov'è."
        return verdict
    if finding.kind == "git-commit":
        match = re.match(r"^skill '([^']+)'", finding.what)
        name = match.group(1) if match else ""
        scope = (skill_scopes or {}).get(name, ".")
        # The key (repo) travels in the label as "(github owner/name)".
        repo_match = re.search(r"\(github ([^)]+)\)", finding.what)
        git_match = re.search(r"\(git ([^)]+)\)", finding.what)
        key = (repo_match or git_match).group(1) if (repo_match or git_match) else ""
        if not key:
            verdict = GuardFinding(what=finding.what, pinned=finding.pinned,
                                   upstream=finding.upstream)
            verdict.reasons.append("cannot tell which repo to inspect")
            verdict.plain = "Non capisco da dove viene: resta fermo."
            return verdict
        return judge_github_skill(finding.what, key, finding.pinned, finding.upstream, scope)
    if finding.kind == "npm-version":
        return judge_npm_package(finding.what, finding.pinned, finding.upstream)
    verdict = GuardFinding(what=finding.what, pinned=finding.pinned,
                           upstream=finding.upstream)
    verdict.reasons.append(f"unknown pin kind '{finding.kind}'")
    verdict.plain = "Tipo sconosciuto: resta fermo."
    return verdict


def _target_of_pin(finding: PinFinding, skill_scopes: dict[str, str] | None) -> dict | None:
    """Where the bump command must rewrite this pin, if it may.

    Only precise targets come back: a vague target is a held item, never
    a rewritten file. The target names the exact field (commit, deps.rev,
    install token, deps spec, mcp arg), because one entry can pin the
    same string twice for two different upstreams with two verdicts.
    """
    if finding.kind == "git-commit":
        match = re.match(r"^skill '([^']+)'", finding.what)
        repo_match = re.search(r"\(github ([^)]+)\)", finding.what)
        if match and repo_match:
            name = match.group(1)
            return {"kind": "git-commit", "skill": name,
                    "repo": repo_match.group(1), "field": "commit",
                    "scope": (skill_scopes or {}).get(name, ".")}
        git_match = re.search(r"\(git ([^)]+)\)", finding.what)
        if match and git_match:
            name = match.group(1)
            return {"kind": "git-commit", "skill": name,
                    "repo": git_match.group(1), "field": "deps.rev",
                    "scope": (skill_scopes or {}).get(name, ".")}
        return None
    if finding.kind == "npm-version":
        match = re.match(r"^skill '([^']+)' \(npm ([^)]+)\)", finding.what)
        if match:
            return {"kind": "npm-version", "manifest": "skills",
                    "skill": match.group(1), "package": match.group(2)}
        match = re.match(r"^MCP server '([^']+)' \(npm ([^)]+)\)", finding.what)
        if match:
            return {"kind": "npm-version", "manifest": "mcp",
                    "server": match.group(1), "package": match.group(2)}
    return None


def run_guardian(
    findings: list[PinFinding],
    state_dir: Path,
    skill_scopes: dict[str, str] | None = None,
) -> dict:
    """Judges every stale finding and writes the verdict file. Read-only
    apart from that file: never touches manifests, pins, or the network
    beyond bounded git inspection clones. Never raises, never notifies."""
    verdicts: list[GuardFinding] = []
    targets: list[dict | None] = []
    try:
        stale = [f for f in findings if f.stale and f.upstream is not None]
        for item in stale:
            try:
                verdicts.append(judge_finding(item, skill_scopes))
            except Exception as exc:
                held = GuardFinding(what=item.what, pinned=item.pinned,
                                    upstream=item.upstream or "")
                held.reasons.append(f"judge crashed: {exc}")
                held.plain = "Il controllo si è rotto: resta fermo."
                verdicts.append(held)
            targets.append(_target_of_pin(item, skill_scopes))

        def _item(v: GuardFinding, target: dict | None) -> dict:
            return {"what": v.what, "pinned": v.pinned, "upstream": v.upstream,
                    "reasons": v.reasons, "plain": v.plain, "target": target}

        payload = {
            "checked_at": time.time(),
            "auto": [_item(v, t) for v, t in zip(verdicts, targets) if v.verdict == AUTO],
            "batch": [_item(v, t) for v, t in zip(verdicts, targets) if v.verdict == BATCH],
            "hold": [_item(v, t) for v, t in zip(verdicts, targets) if v.verdict == HOLD],
        }
        sidecar = Path(state_dir) / "nexgen" / "third-party-guard.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return {"ok": True, "auto": len(payload["auto"]),
                "batch": len(payload["batch"]), "hold": len(payload["hold"])}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
