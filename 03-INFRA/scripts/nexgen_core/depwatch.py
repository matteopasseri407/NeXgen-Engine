"""Dependency watch for NeXgen Engine v2.

Watches upstream everything third-party that the layer declares pinned:
``origin: github`` skills pinned to a commit, ``origin: installer`` skills
pinned to a version (read from the ``install`` command), ``origin:
upstream`` skills pinned via their ``deps:`` block (``npx`` spec or ``git``
repo+rev), MCP servers invoked via ``npx package@version``, and the third-party
components a module declares it carries (``upstream:`` in the module catalog:
the n8n and Firecrawl images, the Playwright version inside its launcher). It
also reports a pinned version its publisher has withdrawn support for:
"nothing newer exists" is not the same as "all good". It produces a
list and stops there: applying an upstream update changes a behavior
nobody chose.

NEVER notifies (no alerts, ever). Being offline is not an incident: if no
check reaches upstream, it writes nothing and reports nothing, because a
workstation is offline all the time. The produced report
(``third-party-upgrades.md``) plus its machine-readable sidecar
(``nexgen/third-party-status.json``, same stale set for the
update-notifier lanes) live in the machine-local state folder and never
sync. Stdlib only, always with short timeouts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nexgen_core.files import atomic_write_text
from nexgen_core.config import ConfigError, load_mcp_manifest, load_skills_manifest
from nexgen_core.modules import ModuleUpstream, external_paths, load_catalog, load_state_file
from nexgen_core.paths import mcp_manifest, resolve_engine_root, resolve_state_dir, resolve_vault_data, skills_manifest

logger = logging.getLogger(__name__)

GIT_LS_REMOTE_TIMEOUT_SECONDS = 8
NPM_REGISTRY_TIMEOUT_SECONDS = 6
REPORT_FILE_NAME = "third-party-upgrades.md"
STATUS_FILE_NAME = "third-party-status.json"

#: An npm-style `package@version` token, scoped or not; `@latest` or a range is not a pin.
NPM_SPEC_RE = re.compile(r"^(?P<name>(?:@[\w.-]+/)?[\w.-]+)@(?P<version>\d[\w.+-]*)$")


@dataclass
class PinFinding:
    kind: str  # "git-commit" | "npm-version" | "docker-image" | "manual-version"
    what: str
    pinned: str
    upstream: str | None
    stale: bool
    #: What the publisher says about this exact version being withdrawn, if it does.
    deprecated: str | None = None


@dataclass
class DepwatchResult:
    findings: list[PinFinding] = field(default_factory=list)
    report_path: Path | None = None
    wrote: bool = False


def _is_stale(kind: str, pinned: str, upstream: str | None, compare: str = "patch") -> bool:
    """True when upstream actually moved past the pin.

    Commits have no ordering here, so any reachable difference counts.
    Versions compare properly: a pin ahead of the registry (vendor
    installer faster than npm) is current, not stale. ``compare="minor"`` is for a rolling
    release whose patch number is a build counter: only a newer minor line is news.
    """
    if upstream is None:
        return False
    if kind in ("npm-version", "docker-image"):
        if pinned.strip().lower() == "latest":
            return False
        old, new = NPM_SPEC_RE.match(f"x@{pinned.strip()}"), NPM_SPEC_RE.match(f"x@{upstream.strip()}")
        if old and new:
            mine = re.match(r"(\d+)\.(\d+)\.(\d+)", old.group("version"))
            theirs = re.match(r"(\d+)\.(\d+)\.(\d+)", new.group("version"))
            if mine and theirs:
                keep = 2 if compare == "minor" else 3
                return (tuple(int(g) for g in theirs.groups())[:keep]
                        > tuple(int(g) for g in mine.groups())[:keep])
            # Non-strict semver (beta/build metadata): hold instead of flapping.
            return False
    return upstream.strip().lower() != pinned.strip().lower()


def _git_ls_remote_head(repo: str) -> str | None:
    """The remote HEAD commit of `repo`, or None if unreachable right now.

    A manifest names a GitHub skill the way people write it
    (``owner/name``); that shorthand is resolved to a clone URL first,
    mirroring what the materializer clones, otherwise every shorthand
    skill lands in "could not be checked" forever.
    """
    try:
        from nexgen_core.skill_sources import clone_url

        target = clone_url(repo)
    except Exception as exc:  # noqa: BLE001 - clone_url is total; defensive fallback keeps the check offline-safe
        logger.debug("clone_url failed (%s)", type(exc).__name__)
        target = repo
    try:
        result = subprocess.run(
            ["git", "ls-remote", target, "HEAD"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            timeout=GIT_LS_REMOTE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[0].strip()


def _npm_latest_version(package: str) -> str | None:
    """The latest published version of `package` on npm, or None if offline."""
    url = f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@')}/latest"
    try:
        with urllib.request.urlopen(url, timeout=NPM_REGISTRY_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


_STRICT_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _git_latest_tag(repo: str) -> str | None:
    """The highest plain ``X.Y.Z`` tag of a GitHub repository, or None if unreachable right now."""
    try:
        from nexgen_core.skill_sources import clone_url

        target = clone_url(repo)
    except Exception as exc:  # noqa: BLE001 - clone_url is total; defensive fallback keeps the check offline-safe
        logger.debug("clone_url failed (%s)", type(exc).__name__)
        target = repo
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", target],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            timeout=GIT_LS_REMOTE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    best: tuple[int, int, int] | None = None
    for line in result.stdout.splitlines():
        match = _STRICT_TAG_RE.match(line.rpartition("refs/tags/")[2].strip())
        if match:
            version = tuple(int(g) for g in match.groups())
            best = version if best is None or version > best else best
    return ".".join(str(n) for n in best) if best else None


def _npm_deprecation(package: str, version: str) -> str | None:
    """The publisher's own words if this exact version is withdrawn (npm `deprecated`), else None.

    Offline, unreachable and "not deprecated" all read as None: the watch never invents a warning.
    """
    name = urllib.parse.quote(package, safe="@")
    url = f"https://registry.npmjs.org/{name}/{urllib.parse.quote(version)}"
    try:
        with urllib.request.urlopen(url, timeout=NPM_REGISTRY_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    notice = data.get("deprecated") if isinstance(data, dict) else None
    return str(notice).strip()[:200] or None if isinstance(notice, str) else None


def _npm_spec_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if isinstance(t, str) and NPM_SPEC_RE.match(t)]


def _command_tokens(srv: dict) -> list[str]:
    """The command line declared by an MCP server, flattened."""
    cmd = srv.get("command") or srv.get("cmd")
    args = srv.get("args", [])
    tokens: list[str] = [str(c) for c in cmd] if isinstance(cmd, list) else [str(cmd)] if cmd else []
    if isinstance(args, list):
        tokens.extend(str(a) for a in args)
    return tokens


def _collect_skill_pins(skills_raw: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """(label, kind, pin, key) for every pinned github, installer or upstream skill.

    ``installer`` pins come from ``npx pkg@version`` tokens in the install
    command; ``upstream`` (and any other origin carrying one) pins come
    from the ``deps:`` block the doctor already verifies offline-safe:
    ``npx`` kind via its ``spec``, ``git`` kind via ``repo``+``rev``.
    """
    pins: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(label: str, kind: str, pin: str, key: str) -> None:
        marker = (label, kind, key.strip().lower(), pin.strip().lower())
        if marker in seen:
            return
        seen.add(marker)
        pins.append((label, kind, pin, key))

    for name, entry in skills_raw.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("origin") == "github":
            repo, commit = entry.get("repo"), entry.get("commit")
            if repo and commit:
                _add(f"skill '{name}' (github {repo})", "git-commit", str(commit), str(repo))
        if entry.get("origin") == "installer":
            install = entry.get("install")
            tokens = [str(t) for t in install] if isinstance(install, list) else []
            specs = _npm_spec_tokens(tokens)
            for spec in specs:
                match = NPM_SPEC_RE.match(spec)
                if match:
                    pkg, ver = match.group("name"), match.group("version")
                    _add(f"skill '{name}' (npm {pkg})", "npm-version", ver, pkg)
            if not specs and entry.get("version"):
                # pip/uvx/shell installers carry no npm token: without this
                # entry the pin would rot forever with nobody watching.
                # Surfaced as manually-watched (no upstream resolver), so at
                # least the report names it instead of silently ignoring it.
                _add(f"skill '{name}' (installer)", "manual-version", str(entry.get("version")), name)
        deps = entry.get("deps")
        if isinstance(deps, dict):
            kind = str(deps.get("kind") or "").strip()
            if kind == "npx":
                spec = str(deps.get("spec") or "").strip()
                match = NPM_SPEC_RE.match(spec)
                if match:
                    pkg, ver = match.group("name"), match.group("version")
                    _add(f"skill '{name}' (npm {pkg})", "npm-version", ver, pkg)
            elif kind == "git":
                repo, rev = deps.get("repo"), deps.get("rev")
                if repo and rev:
                    _add(f"skill '{name}' (git {repo})", "git-commit", str(rev), str(repo))
    return pins


def _collect_mcp_pins(mcp_raw: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """(label, kind, pin, key) for every MCP server invoked via npx.

    A launcher script that pins its own package (the Playwright wrapper) is not read from here:
    its version is declared once, by the module that ships it. Rewriting a number in a manifest
    cannot change the one inside the script, so a second declaration could only drift.
    """
    pins: list[tuple[str, str, str, str]] = []
    for name, srv in mcp_raw.items():
        if not isinstance(srv, dict):
            continue
        tokens = _command_tokens(srv)
        if not tokens or tokens[0].lower() not in ("npx", "npx.cmd"):
            continue
        for spec in _npm_spec_tokens(tokens[1:]):
            match = NPM_SPEC_RE.match(spec)
            if match:
                pkg, ver = match.group("name"), match.group("version")
                pins.append((f"MCP server '{name}' (npm {pkg})", "npm-version", ver, pkg))
    return pins


@dataclass(frozen=True)
class _ModulePin:
    """One third-party component of a module that this machine switched on."""

    label: str
    kind: str  # "docker-image" | "npm-version"
    pinned: str
    latest: str  # "npm:<package>" | "git-tags:<owner/repo>"
    compare: str
    package: str = ""


def _module_pin_version(base: Path, upstream: ModuleUpstream) -> str | None:
    """The version a module's component is pinned to, read from the file that holds the pin.

    None when the file is gone, escapes its owner through a link, or no longer has the shape the
    declaration describes: a pin that cannot be read is not watched, and a test of the catalog
    shipped with the engine fails first.
    """
    try:
        root = base.resolve()
        file = (base / upstream.pinned_in).resolve()
        file.relative_to(root)
        text = file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    if upstream.kind == "docker":
        wrapped = r"(?:\$\{[A-Za-z0-9_]+:-)?"
        pattern = rf"(?m)^\s*image:\s*{wrapped}{re.escape(upstream.image)}:(\d[\w.+-]*)\}}?\s*$"
    else:
        pattern = upstream.pattern
    found = re.search(pattern, text)
    return found.group(1) if found else None


def _collect_module_pins(engine_root: Path, vault_data: Path) -> list[_ModulePin]:
    """The components of every module this machine declared on (local or remote).

    Declared, not merely gated: a heartbeat started without a service's token would otherwise
    see the module vanish and the finding with it, then reappear, and the notice would flicker.
    A module nobody switched on is nobody's concern here.
    """
    try:
        catalog = load_catalog(engine_root, external=external_paths(vault_data))
        declared = load_state_file(vault_data)
    except Exception as exc:  # noqa: BLE001 - no readable catalog just means no module pins
        logger.debug("module catalog not readable (%s)", type(exc).__name__)
        return []
    pins: list[_ModulePin] = []
    for module_id, module in catalog.items():
        if declared.get(module_id) not in ("local", "remote"):
            continue
        base = module.source_path or engine_root
        for upstream in module.upstream:
            pinned = _module_pin_version(base, upstream)
            if pinned is None:
                continue
            if upstream.kind == "docker":
                what = f"{upstream.image}, the engine's default image"
                package = ""
            else:
                what = upstream.package
                package = upstream.package
            pins.append(_ModulePin(
                label=f"module '{module_id}' ({upstream.kind} {what})",
                kind="docker-image" if upstream.kind == "docker" else "npm-version",
                pinned=pinned, latest=upstream.latest, compare=upstream.compare, package=package,
            ))
    return pins


def _write_report(path: Path, findings: list[PinFinding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = [
        ("Moved upstream", [f for f in findings if f.stale],
         lambda f: f"- {f.what}: pinned `{f.pinned}` -> upstream `{f.upstream}`"),
        ("Up to date", [f for f in findings if f.upstream is not None and not f.stale],
         lambda f: f"- {f.what}: `{f.pinned}`"),
        ("Deprecated upstream", [f for f in findings if f.deprecated],
         lambda f: f"- {f.what}: pinned `{f.pinned}`, the publisher says: {f.deprecated}"),
        ("Manually watched (no upstream check)", [f for f in findings if f.kind == "manual-version"],
         lambda f: f"- {f.what}: pinned `{f.pinned}` (no resolver covers this installer: check upstream by hand)"),
        ("Could not be checked this run", [f for f in findings if f.upstream is None and f.kind != "manual-version"],
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
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def run_depwatch(
    *,
    vault_data: Path | None = None,
    state_dir: Path | None = None,
    git_ls_remote: Callable[[str], str | None] | None = None,
    npm_latest_version: Callable[[str], str | None] | None = None,
    npm_deprecation: Callable[[str, str], str | None] | None = None,
    git_latest_tag: Callable[[str], str | None] | None = None,
) -> DepwatchResult:
    """Inspects every declared pin upstream and writes the list, without ever
    applying or notifying anything. If no check reaches upstream (offline, or
    nothing to watch), it writes and reports nothing.

    Resolvers default late (not in the signature) so tests can fake the
    network by patching this module.
    """
    git_check = git_ls_remote or _git_ls_remote_head
    npm_check = npm_latest_version or _npm_latest_version
    deprecation_check = npm_deprecation or _npm_deprecation
    tag_check = git_latest_tag or _git_latest_tag
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

    try:
        module_pins = _collect_module_pins(resolve_engine_root(), resolved_vault)
    except Exception as exc:  # noqa: BLE001 - no engine tree to read (a bare install) just means no module pins
        logger.debug("module pins not collected (%s)", type(exc).__name__)
        module_pins = []
    pins = _collect_skill_pins(skills_raw) + _collect_mcp_pins(mcp_raw)
    findings: list[PinFinding] = []
    for what, kind, pinned, key in pins:
        if kind == "manual-version":
            findings.append(PinFinding(kind=kind, what=what, pinned=pinned, upstream=None, stale=False))
            continue
        upstream = git_check(key) if kind == "git-commit" else npm_check(key)
        stale = _is_stale(kind, pinned, upstream)
        # Asked only when the registry answered at all, and only for a package a person pinned.
        deprecated = deprecation_check(key, pinned) if kind == "npm-version" and upstream is not None else None
        findings.append(PinFinding(kind=kind, what=what, pinned=pinned, upstream=upstream, stale=stale, deprecated=deprecated))
    for mp in module_pins:
        source, _, argument = mp.latest.partition(":")
        newest = tag_check(argument) if source == "git-tags" else npm_check(argument)
        deprecated = deprecation_check(mp.package, mp.pinned) if mp.package and newest is not None else None
        findings.append(PinFinding(
            kind=mp.kind, what=mp.label, pinned=mp.pinned, upstream=newest,
            stale=_is_stale(mp.kind, mp.pinned, newest, mp.compare), deprecated=deprecated,
        ))

    if not any(f.upstream is not None for f in findings):
        # Nothing pinned, or upstream isn't responding right now: offline is not an incident, stay quiet.
        return DepwatchResult(findings=findings, report_path=None, wrote=False)

    report_path = resolved_state / REPORT_FILE_NAME
    _write_report(report_path, findings)
    _write_status_sidecar(resolved_state, findings)
    return DepwatchResult(findings=findings, report_path=report_path, wrote=True)


def _write_status_sidecar(state_dir: Path, findings: list[PinFinding]) -> None:
    """Machine-readable twin of the report for the update-notifier lanes.

    The shell hook and the GUI timer must never touch the network: they
    read this file only. Written exactly when the report is written, so
    "no sidecar" unambiguously means "nothing fresh to show".
    """
    try:
        stale = sorted(f"{f.what}|{f.pinned}|{f.upstream}" for f in findings if f.stale)
        fingerprint = hashlib.sha256("\n".join(stale).encode()).hexdigest()[:16]
        payload = {
            "checked_at": time.time(),
            "stale_count": len(stale),
            "stale": sorted(f.what for f in findings if f.stale),
            "fingerprint": fingerprint,
            "report": REPORT_FILE_NAME,
            # Every pin with what upstream said, so `nexgen info` can show it without the network.
            "pins": [
                {"what": f.what, "kind": f.kind, "pinned": f.pinned, "upstream": f.upstream, "stale": f.stale,
                 **({"deprecated": f.deprecated} if f.deprecated else {})}
                for f in findings
            ],
        }
        sidecar = state_dir / "nexgen" / STATUS_FILE_NAME
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(sidecar, json.dumps(payload, indent=2) + "\n")
    except OSError:
        pass
