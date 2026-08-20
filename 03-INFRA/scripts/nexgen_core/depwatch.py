"""La sorveglianza delle dipendenze per NeXgen Engine v2.

Guarda a monte ogni cosa di terzi che il layer dichiara pinnata: skill
``origin: github`` pinnate a un commit, skill ``origin: installer`` pinnate a
una versione (letta dal comando ``install``), server MCP invocati via
``npx pacchetto@versione``. Produce una lista e si ferma lì: applicare un
aggiornamento a monte cambia un comportamento che nessuno ha scelto.

Non notifica MAI (niente Megafono, mai). Essere offline non è un incidente:
se nessun controllo raggiunge l'upstream, non scrive e non riporta nulla,
perché una workstation è offline in continuazione. Il report prodotto
(``third-party-upgrades.md``) vive nella cartella di stato locale alla
macchina e non si sincronizza mai. Solo stdlib, sempre con timeout brevi.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nexgen_core.config import ConfigError, load_mcp_manifest, load_skills_manifest
from nexgen_core.paths import mcp_manifest, resolve_state_dir, resolve_vault_data, skills_manifest

GIT_LS_REMOTE_TIMEOUT_SECONDS = 8
NPM_REGISTRY_TIMEOUT_SECONDS = 6
REPORT_FILE_NAME = "third-party-upgrades.md"

#: Un token `pacchetto@versione` in stile npm, scoped o no; `@latest` o un range non è un pin.
NPM_SPEC_RE = re.compile(r"^(?P<name>(?:@[\w.-]+/)?[\w.-]+)@(?P<version>\d[\w.+-]*)$")


@dataclass
class PinFinding:
    kind: str  # "git-commit" | "npm-version"
    what: str
    pinned: str
    upstream: str | None
    stale: bool


@dataclass
class DepwatchResult:
    findings: list[PinFinding] = field(default_factory=list)
    report_path: Path | None = None
    wrote: bool = False


def _git_ls_remote_head(repo: str) -> str | None:
    """Il commit HEAD remoto di `repo`, o None se non raggiungibile ora."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", repo, "HEAD"],
            capture_output=True, text=True, check=False,
            timeout=GIT_LS_REMOTE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[0].strip()


def _npm_latest_version(package: str) -> str | None:
    """L'ultima versione pubblicata di `package` su npm, o None se offline."""
    url = f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@')}/latest"
    try:
        with urllib.request.urlopen(url, timeout=NPM_REGISTRY_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


def _npm_spec_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if isinstance(t, str) and NPM_SPEC_RE.match(t)]


def _command_tokens(srv: dict) -> list[str]:
    """La riga di comando dichiarata da un server MCP, appiattita."""
    cmd = srv.get("command") or srv.get("cmd")
    args = srv.get("args", [])
    tokens: list[str] = [str(c) for c in cmd] if isinstance(cmd, list) else [str(cmd)] if cmd else []
    if isinstance(args, list):
        tokens.extend(str(a) for a in args)
    return tokens


def _collect_skill_pins(skills_raw: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """(etichetta, tipo, pin, chiave) per ogni skill github o installer pinnata."""
    pins: list[tuple[str, str, str, str]] = []
    for name, entry in skills_raw.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("origin") == "github":
            repo, commit = entry.get("repo"), entry.get("commit")
            if repo and commit:
                pins.append((f"skill '{name}' (github {repo})", "git-commit", str(commit), str(repo)))
        elif entry.get("origin") == "installer":
            install = entry.get("install")
            tokens = [str(t) for t in install] if isinstance(install, list) else []
            for spec in _npm_spec_tokens(tokens):
                match = NPM_SPEC_RE.match(spec)
                pkg, ver = match.group("name"), match.group("version")
                pins.append((f"skill '{name}' (npm {pkg})", "npm-version", ver, pkg))
    return pins


def _collect_mcp_pins(mcp_raw: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """(etichetta, tipo, pin, chiave) per ogni server MCP invocato via npx."""
    pins: list[tuple[str, str, str, str]] = []
    for name, srv in mcp_raw.items():
        if not isinstance(srv, dict):
            continue
        tokens = _command_tokens(srv)
        if not tokens or tokens[0].lower() not in ("npx", "npx.cmd"):
            continue
        for spec in _npm_spec_tokens(tokens[1:]):
            match = NPM_SPEC_RE.match(spec)
            pkg, ver = match.group("name"), match.group("version")
            pins.append((f"MCP server '{name}' (npm {pkg})", "npm-version", ver, pkg))
    return pins


def _write_report(path: Path, findings: list[PinFinding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = [
        ("Moved upstream", [f for f in findings if f.stale],
         lambda f: f"- {f.what}: pinned `{f.pinned}` -> upstream `{f.upstream}`"),
        ("Up to date", [f for f in findings if f.upstream is not None and not f.stale],
         lambda f: f"- {f.what}: `{f.pinned}`"),
        ("Could not be checked this run", [f for f in findings if f.upstream is None],
         lambda f: f"- {f.what}: pinned `{f.pinned}`"),
    ]
    intro = (
        "Dependency Watch only lists what moved upstream since the pin in the "
        "manifest. It never applies anything: raising a pin is a deliberate edit "
        "made by a person."
    )
    lines = ["# Third-party upgrades", "", intro, ""]
    for heading, rows, fmt in sections:
        if not rows:
            continue
        lines.append(f"## {heading}")
        lines.extend(fmt(row) for row in rows)
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run_depwatch(
    *,
    vault_data: Path | None = None,
    state_dir: Path | None = None,
    git_ls_remote: Callable[[str], str | None] = _git_ls_remote_head,
    npm_latest_version: Callable[[str], str | None] = _npm_latest_version,
) -> DepwatchResult:
    """Ispeziona a monte ogni pin dichiarato e scrive la lista, senza mai
    applicare o notificare nulla. Se nessun controllo raggiunge l'upstream
    (offline, o niente da guardare) non scrive e non riporta niente.
    """
    resolved_vault = resolve_vault_data(override=vault_data)
    resolved_state = resolve_state_dir(override=state_dir)

    skills_raw: dict[str, dict] = {}
    skills_path = skills_manifest(resolved_vault)
    if skills_path.is_file():
        try:
            skills_raw = load_skills_manifest(skills_path).get("skills", {})
        except ConfigError:
            skills_raw = {}

    mcp_raw: dict[str, dict] = {}
    mcp_path = mcp_manifest(resolved_vault)
    if mcp_path.is_file():
        try:
            mcp_raw = load_mcp_manifest(mcp_path).get("servers", {})
        except ConfigError:
            mcp_raw = {}

    pins = _collect_skill_pins(skills_raw) + _collect_mcp_pins(mcp_raw)
    findings: list[PinFinding] = []
    for what, kind, pinned, key in pins:
        upstream = git_ls_remote(key) if kind == "git-commit" else npm_latest_version(key)
        stale = upstream is not None and upstream.strip().lower() != pinned.strip().lower()
        findings.append(PinFinding(kind=kind, what=what, pinned=pinned, upstream=upstream, stale=stale))

    if not any(f.upstream is not None for f in findings):
        # Nulla pinnato, o l'upstream non risponde ora: offline non è un incidente, silenzio.
        return DepwatchResult(findings=findings, report_path=None, wrote=False)

    report_path = resolved_state / REPORT_FILE_NAME
    _write_report(report_path, findings)
    return DepwatchResult(findings=findings, report_path=report_path, wrote=True)
