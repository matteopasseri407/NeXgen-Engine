"""Visual system status banner and info screen for NeXgen Engine.

Styled according to the NeXgen Engine design system (Emerald #00E5B8 & Slate Dark),
providing an informative, human-readable terminal dashboard.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from nexgen_core import __version__
from nexgen_core.i18n import t
from nexgen_core.modules import modules_state
from nexgen_core.paths import (
    resolve_engine_root,
    resolve_home,
    resolve_vault_data,
)


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


USE_COLOR = _supports_color()


def _c(code: str) -> str:
    return code if USE_COLOR else ""


C_RESET = _c("\033[0m")
C_BOLD = _c("\033[1m")
C_DIM = _c("\033[2m")
C_EMERALD = _c("\033[38;2;0;229;184m")     # #00E5B8
C_SLATE = _c("\033[38;2;130;138;148m")      # #828A94
C_DARK_SLATE = _c("\033[38;2;60;66;75m")   # #3C424B
C_WHITE = _c("\033[38;2;241;241;237m")     # #F1F1ED
C_GREEN = _c("\033[38;2;52;211;153m")      # #34D399
C_CYAN = _c("\033[38;2;56;189;248m")       # #38BDF8
C_YELLOW = _c("\033[38;2;251;191;36m")     # #FBBF24

LOGO_LINES = [
    r"  \ \   \       / / ",
    r"   \ \   \     / /  ",
    r"    \ \   \   / /   ",
    r"     \ \   \ / /    ",
    r"      \ \   X /     ",
    r"      / /  / \ \    ",
    r"     / /  /   \ \   ",
    r"    / /  /     \ \  ",
]


def get_engine_info(vault_data: Path | None = None) -> dict[str, Any]:
    """Gathers structured system and engine state."""
    home = resolve_home()
    vault = resolve_vault_data(home, vault_data)
    engine_root = resolve_engine_root(home)

    # Runtimes detected
    runtimes = []
    if (home / ".claude").exists() or (home / ".claude.json").exists():
        runtimes.append("Claude Code")
    if (home / ".codex").exists() or shutil.which("codex"):
        runtimes.append("Codex")
    if shutil.which("opencode") or (home / ".config" / "opencode").exists():
        runtimes.append("OpenCode")
    if shutil.which("agy") or (home / ".gemini" / "antigravity-ide").exists():
        runtimes.append("Antigravity")
    if not runtimes:
        runtimes = ["Standard CLI"]

    # Modules
    m_states = []
    try:
        m_states = modules_state(vault, engine_root)
    except Exception:  # noqa: BLE001 - info tolerates unreadable state
        pass

    # Secrets store check
    secrets_age = vault / "99-SECRETS" / "secrets.yaml.age"
    secrets_env = home / ".config" / "nexgen" / "secrets.env"
    secrets_key = home / ".config" / "nexgen" / "secrets" / "identity.agekey"

    secrets_status = "unconfigured"
    if secrets_age.is_file():
        if secrets_key.is_file() and secrets_env.is_file():
            secrets_status = "age multi-recipient (zero-passphrase, active)"
        elif secrets_key.is_file():
            secrets_status = "age (enrolled, needs materialize)"
        else:
            secrets_status = "age (encrypted, unenrolled host)"

    # Doctor quick summary
    doctor_status = "operational"
    try:
        from nexgen_core.doctor import Doctor
        report = Doctor(vault_data=vault, home=home, engine_root=engine_root).run_diagnostics()
        if report.is_healthy:
            doctor_status = f"{report.ok_count}/{len(report.outcomes)} checks pass (healthy)"
        else:
            doctor_status = f"{report.ok_count}/{len(report.outcomes)} pass ({len(report.broken)} broken)"
        if report.warnings:
            doctor_status += f", {len(report.warnings)} warnings"
    except Exception:  # noqa: BLE001 - info tolerates unreadable state
        doctor_status = "not evaluated"

    # Notes count
    notes_count = 0
    if vault.is_dir():
        try:
            notes_count = len(list(vault.rglob("*.md")))
        except OSError:
            pass

    extensions: dict[str, Any] = {}
    try:
        from nexgen_core import extensions as ext

        extensions = ext.collect(home=home, vault_data=vault, engine_root=engine_root)
    except Exception:  # noqa: BLE001 - info tolerates unreadable state
        extensions = {"errors": [t("could not read the installed connectors and skills")]}

    return {
        "version": __version__,
        "os": f"{platform.system()} {platform.machine()}",
        "host": platform.node() or "localhost",
        "python": sys.version.split()[0],
        "vault_path": str(vault),
        "engine_root": str(engine_root),
        "runtimes": runtimes,
        "modules": [(m.module.id, m.state, m.note) for m in m_states],
        "secrets": secrets_status,
        "doctor": doctor_status,
        "notes_count": notes_count,
        "extensions": extensions,
    }


_PROVENANCE_LABEL = {
    "engine": lambda: t("core"),
    "yours": lambda: t("yours"),
    "third-party": lambda: t("third-party"),
}


#: Why a server is mounted nowhere, in words (the placement rule's own reasons are for `nexgen mcp plan`).
_WHY_OFF = {
    "not in targets": lambda: t("no CLI is in its targets"),
    "disabled": lambda: t("switched off in the manifest"),
    "inert (not core, not enabled)": lambda: t("neither core nor switched on"),
}

_WHERE_LABEL = {
    "direct": lambda: t("direct"),
    "gateway": lambda: t("lazy"),
    "mixed": lambda: t("mixed"),
    "off": lambda: t("off"),
}


def _mix(counted: dict[str, int]) -> str:
    return " · ".join(f"{n} {_PROVENANCE_LABEL[k]()}" for k, n in counted.items() if n)


def _extension_lines(ext: dict[str, Any], show_all: bool) -> list[str]:
    """The connectors and skills sections: what is installed, whose it is, what is pinned, what moved upstream."""
    from nexgen_core import extensions

    out: list[str] = []
    mcp, skills = ext.get("mcp", []), ext.get("skills", [])
    for error in ext.get("errors", []):
        out.append(f"  {C_YELLOW}! {error}{C_RESET}")

    out.append(f"  {C_EMERALD}{C_BOLD}{t('CONNECTORS (MCP)')}{C_RESET}  {C_SLATE}{len(mcp)} · {_mix(extensions.counts(mcp))}{C_RESET}")
    if mcp:
        width = max(len(r["name"]) for r in mcp)
        for r in mcp:
            where = _WHERE_LABEL[r["where"]]() if r["state"] != "trial" else t("trial")
            pin = r["pin"] or ("" if r["pin_state"] == "none" else "?")
            origin = _PROVENANCE_LABEL[r["provenance"]]()
            limited = f" ({', '.join(r['clis'])})" if r["clis"] and len(r["clis"]) < len(r["placement"]) else ""
            out.append(f"  {C_SLATE}•{C_RESET} {r['name'].ljust(width)}  {origin.ljust(11)} {where.ljust(8)} {C_DIM}{pin}{limited}{C_RESET}")
            notes = _mcp_notes(r)
            for note in notes:
                out.append(f"      {C_YELLOW}↳ {note}{C_RESET}")
    out.append("")

    out.append(f"  {C_EMERALD}{C_BOLD}{t('SKILLS')}{C_RESET}  {C_SLATE}{len(skills)} · {_mix(extensions.counts(skills))}{C_RESET}")
    shown = [r for r in skills if show_all or r["provenance"] == "third-party" or r["engine_copy"] is not None
             or not r["in_library"] or r["update"].get("state") == "stale"]
    if shown:
        width = max(len(r["name"]) for r in shown)
        for r in shown:
            out.append(f"  {C_SLATE}•{C_RESET} {r['name'].ljust(width)}  {_PROVENANCE_LABEL[r['provenance']]().ljust(11)} {C_DIM}{r['pin']}{C_RESET}")
            for note in _skill_notes(r):
                out.append(f"      {C_YELLOW}↳ {note}{C_RESET}")
    hidden = len(skills) - len(shown)
    if hidden > 0:
        out.append(f"  {C_DIM}{t('{count} more (yours, or core ones that follow the engine): nexgen info --all', count=hidden)}{C_RESET}")
    if ext.get("skills_outside_manifest"):
        out.append(f"  {C_DIM}{t('outside the manifest (kept, never deleted): {names}', names=', '.join(ext['skills_outside_manifest']))}{C_RESET}")
    out.append("")

    modules = ext.get("modules", [])
    if modules:
        out.append(f"  {C_EMERALD}{C_BOLD}{t('THIRD-PARTY INSIDE MODULES')}{C_RESET}  {C_SLATE}{len(modules)}{C_RESET}")
        width = max(len(r["name"]) for r in modules)
        for r in modules:
            out.append(f"  {C_SLATE}•{C_RESET} {r['name'].ljust(width)}  {C_DIM}{r['component']}  {r['pinned']}{C_RESET}")
            for note in _module_notes(r):
                out.append(f"      {C_YELLOW}↳ {note}{C_RESET}")
        out.append("")

    moved = extensions.updates(ext)
    age = extensions.age_text(ext.get("upstream_checked_at"))
    if moved:
        out.append(f"  {C_YELLOW}{t('Newer upstream versions exist for third-party items: {count} (checked {age} ago).', count=len(moved), age=age or '?')}{C_RESET}")
        out.append(f"  {C_DIM}{t('nexgen skills bump  (or: nexgen mcp bump)  raises the ones the guardian cleared, tries each server and puts the old pin back if it fails.')}{C_RESET}")
    elif age is None and any(r["update"].get("state") == "unknown" for r in mcp + skills):
        out.append(f"  {C_DIM}{t('Upstream versions not checked on this machine yet: the hourly watch will, or run nexgen skills bump.')}{C_RESET}")
    else:
        out.append(f"  {C_DIM}{t('Third-party pins are current (checked {age} ago).', age=age or '?')}{C_RESET}")
    withdrawn = extensions.deprecations(ext)
    if withdrawn:
        out.append(f"  {C_YELLOW}{t('Support withdrawn by the publisher: {names}', names=', '.join(d['name'] for d in withdrawn))}{C_RESET}")
    out.append("")
    return out


def _mcp_notes(r: dict[str, Any]) -> list[str]:
    notes = []
    update = r["update"]
    if update.get("state") == "stale":
        line = t("upstream {new} available (pinned {old})", new=update.get("upstream") or "?", old=update.get("pinned") or "?")
        if update.get("verdict") == "held":
            line += " — " + t("held: {why}", why=update.get("plain") or t("read the changelog first"))
        elif update.get("verdict") == "ready":
            line += " — " + t("cleared, ready to raise")
        notes.append(line)
    if update.get("deprecated"):
        notes.append(t("its publisher withdrew support for this version: {why}", why=update["deprecated"]))
    if r["pin_state"] == "unpinned":
        notes.append(t("not pinned: runs whatever the registry serves today, and nothing watches it"))
    if r["hidden_tools"]:
        notes.append(t("{count} tools hidden", count=r["hidden_tools"]))
    if r["only_tools"]:
        notes.append(t("only {count} tools exposed", count=r["only_tools"]))
    if r["where"] == "off" and r["why_off"]:
        notes.append(t("not mounted anywhere: {why}", why=_WHY_OFF.get(r["why_off"], lambda: r["why_off"])()))
    return notes


def _module_notes(r: dict[str, Any]) -> list[str]:
    notes = []
    update = r["update"]
    if update.get("state") == "stale":
        if r["kind"] == "docker-image":
            # The engine's shipped default, not what any one server runs: say so instead of "you have".
            line = t("upstream {new} available (the engine's default image is {old}; the server running it may differ)",
                     new=update.get("upstream") or "?", old=update.get("pinned") or "?")
        else:
            line = t("upstream {new} available (pinned {old})", new=update.get("upstream") or "?", old=update.get("pinned") or "?")
        if update.get("verdict") == "held":
            line += " — " + t("held: {why}", why=update.get("plain") or t("read the changelog first"))
        notes.append(line)
    if update.get("deprecated"):
        notes.append(t("its publisher withdrew support for this version: {why}", why=update["deprecated"]))
    return notes


def _skill_notes(r: dict[str, Any]) -> list[str]:
    notes = []
    update = r["update"]
    if update.get("state") == "stale":
        notes.append(t("upstream {new} available (pinned {old})", new=update.get("upstream") or "?", old=update.get("pinned") or "?"))
    if update.get("deprecated"):
        notes.append(t("its publisher withdrew support for this version: {why}", why=update["deprecated"]))
    if r["engine_copy"] == "differs":
        notes.append(t("your Vault's copy differs from the engine's, and the copy is what runs"))
    elif r["engine_copy"] == "same":
        notes.append(t("a frozen copy in your Vault, identical to the engine's today: nexgen skills adopt"))
    if not r["in_library"]:
        notes.append(t("declared but not materialized on this machine yet"))
    return notes


def render_info(as_json: bool = False, vault_data: Path | None = None, show_all: bool = False) -> str:
    info = get_engine_info(vault_data)
    if as_json:
        return json.dumps(info, indent=2)

    out = []
    out.append("")

    header_right = [
        f"{C_BOLD}{C_WHITE}neXgen Engine{C_RESET}  {C_EMERALD}v{info['version']}{C_RESET}",
        f"{C_SLATE}AI OPERATING LAYER{C_RESET}",
        "",
        f"{C_WHITE}One Canonical Source. Any Agent. Always in Sync.{C_RESET}",
        f"{C_EMERALD}[{C_RESET} {C_BOLD}BEHAVIOR{C_RESET} {C_EMERALD}]{C_RESET}  {C_EMERALD}[{C_RESET} {C_BOLD}CONFIGURATION{C_RESET} {C_EMERALD}]{C_RESET}  {C_EMERALD}[{C_RESET} {C_BOLD}MEMORY{C_RESET} {C_EMERALD}]{C_RESET}",
        "",
        f"{C_SLATE}Host:{C_RESET} {info['host']} ({info['os']}) · Python {info['python']}",
        f"{C_SLATE}Vault:{C_RESET} {info['vault_path']} ({info['notes_count']} notes)",
    ]

    for i in range(max(len(LOGO_LINES), len(header_right))):
        logo_part = LOGO_LINES[i] if i < len(LOGO_LINES) else " " * 24
        info_part = header_right[i] if i < len(header_right) else ""
        out.append(f"  {C_EMERALD}{logo_part:<24}{C_RESET}   {info_part}")

    out.append("")
    out.append(f"  {C_DARK_SLATE}{'─' * 74}{C_RESET}")
    out.append("")

    # Section 1: The Three Planes
    out.append(f"  {C_EMERALD}{C_BOLD}PLANES & RUNTIMES{C_RESET}")
    runtimes_str = ", ".join(info["runtimes"])
    out.append(f"  {C_SLATE}• Behavior:{C_RESET}      AGENTS.md canonical bootstrap (universal rules)")
    out.append(f"  {C_SLATE}• Configuration:{C_RESET} 4 Target Runtimes ({C_WHITE}{runtimes_str}{C_RESET})")
    out.append(f"  {C_SLATE}• Memory:{C_RESET}        KnowledgeVault (CAS per-section lock, Git versioned)")
    out.append("")

    # Section 2: Modules Overview
    out.append(f"  {C_EMERALD}{C_BOLD}MODULES (CATALOG & PER-MACHINE STATE){C_RESET}")
    if info["modules"]:
        active = [f"{m[0]} ({m[1]})" for m in info["modules"] if m[1] != "absent"]
        absent = [m[0] for m in info["modules"] if m[1] == "absent"]

        active_str = f"{C_GREEN}{', '.join(active)}{C_RESET}" if active else f"{C_SLATE}none{C_RESET}"
        absent_str = f"{C_SLATE}{', '.join(absent)}{C_RESET}" if absent else f"{C_SLATE}none{C_RESET}"

        out.append(f"  {C_SLATE}• Active:{C_RESET}        {active_str}")
        out.append(f"  {C_SLATE}• Standby/Off:{C_RESET}   {absent_str}")
    else:
        out.append(f"  {C_SLATE}• State:{C_RESET}         {C_SLATE}No modules declared yet (run 'nexgen modules list'){C_RESET}")
    out.append("")

    out.extend(_extension_lines(info.get("extensions", {}), show_all))

    # Section 3: Security & Health
    out.append(f"  {C_EMERALD}{C_BOLD}SECURITY & DIAGNOSTICS{C_RESET}")
    out.append(f"  {C_SLATE}• Secrets Store:{C_RESET} {info['secrets']}")
    out.append(f"  {C_SLATE}• Health/Doctor:{C_RESET} {C_GREEN}● {info['doctor']}{C_RESET}")
    out.append("")
    out.append(f"  {C_DARK_SLATE}{'─' * 74}{C_RESET}")
    out.append(f"  {C_DIM}Run 'nexgen doctor' for full diagnosis · 'nexgen modules list' for module matrix{C_RESET}")
    out.append("")

    return "\n".join(out)


def _bootstrap_targets(home: Path) -> list[tuple[str, Path, str]]:
    return [
        ("claude", home / "CLAUDE.md", "pointer"),
        ("codex", home / ".codex" / "AGENTS.md", "mirror"),
        ("antigravity", home / ".gemini" / "config" / "AGENTS.md", "mirror"),
        ("opencode", home / ".config" / "opencode" / "AGENTS.md", "scope"),
    ]


def _instruction_state(path: Path, kind: str, vault_data: Path) -> str:
    """Says not just whether the file is there, but whether it still tells the truth.

    "Present" isn't enough: a real copy that has stopped matching the
    canonical file is exactly the case this inventory needs to catch.
    """
    import hashlib

    from nexgen_core.paths import canonical_instructions

    canon = canonical_instructions(vault_data)
    if not path.exists() and not path.is_symlink():
        return "absent"
    if path.is_symlink():
        try:
            target = path.resolve()
        except OSError as exc:
            return f"unreadable ({exc})"
        if not target.exists():
            return f"broken link -> {target}"
        if kind == "scope":
            canon_file = canon.is_file()
            same = canon_file and target == canon.resolve()
            return f"link -> {target}" + ("" if same else " (not the canonical bootstrap)")
        return f"link -> {target}"
    try:
        body = path.read_bytes()
    except OSError as exc:
        return f"unreadable ({exc})"
    if kind == "scope":
        # V2 loads this file's content as instructions. A real file here is
        # the private identity layer's derivative until proven otherwise:
        # census, not diagnosis -- the doctor owns the verdict.
        if str(canon) in body.decode("utf-8", "replace"):
            return "real file referencing the canonical bootstrap"
        return "real file (verify it references the canonical bootstrap)"
    if kind == "pointer":
        return "pointer" if str(canon) in body.decode("utf-8", "replace") else "diverged: does not name the canonical file"
    if not canon.is_file():
        return "real copy (canonical file doesn't exist, can't compare)"
    same = hashlib.sha256(body).hexdigest() == hashlib.sha256(canon.read_bytes()).hexdigest()
    return "copy aligned" if same else "diverged: real copy differs from the canonical file"


def _native_memory_report(home: Path) -> list[tuple[str, str]]:
    """The memories each runtime keeps on its own, alongside the Vault.

    This isn't a diagnosis: it's a census, because onboarding needs to be
    able to tell the user what already exists before proposing to adopt or
    reset it.
    """
    out: list[tuple[str, str]] = []

    claude_memory = home / ".claude" / "memory"
    if claude_memory.is_dir():
        facts = sum(1 for _ in claude_memory.rglob("*.md"))
        out.append(("claude", t("{count} durable facts in {path}", count=facts, path=claude_memory)))
    else:
        out.append(("claude", t("no native memory")))

    for label, path in (
        ("codex", home / ".codex" / "sessions"),
        ("antigravity", home / ".gemini" / "tmp"),
    ):
        if path.is_dir():
            count = sum(1 for _ in path.rglob("*") if _.is_file())
            out.append((label, t(
                "{count} transcript files in {path} (to be distilled, not structured memory)",
                count=count, path=path,
            )))
        else:
            out.append((label, t("no transcripts")))

    # OpenCode V2 keeps sessions under the XDG data dir (sqlite + sidecars);
    # the pre-V2 `~/.opencode/storage` is only a fallback for machines that
    # never migrated. Reporting the wrong root would census an empty room.
    for candidate in (
        home / ".local" / "share" / "opencode",
        home / ".opencode" / "storage",
    ):
        if candidate.is_dir():
            count = sum(1 for _ in candidate.rglob("*") if _.is_file())
            out.append(("opencode", t(
                "{count} transcript files in {path} (to be distilled, not structured memory)",
                count=count, path=candidate,
            )))
            break
    else:
        out.append(("opencode", t("no transcripts")))

    return out


def inventory_lines(
    home: Path | None = None,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
) -> tuple[list[str], int]:
    """The inventory rows: what is on this machine, against what should be there.

    Single owner of every inventory line: `cli/engine.py` only prints what
    this returns, without duplicating texts or formats.
    """
    from nexgen_core.renderer import McpRenderer
    from nexgen_core.skills import SkillMaterializer

    effective_home = resolve_home(home)
    effective_vault = resolve_vault_data(effective_home, vault_data)
    effective_engine = resolve_engine_root(effective_home, engine_root)
    problems = 0
    lines: list[str] = []

    lines.append(t(">>> MCP connectors per runtime"))
    renderer = McpRenderer(vault_data=effective_vault, engine_root=effective_engine, home=effective_home)
    for cli_name in ("claude", "codex", "antigravity", "opencode"):
        servers = renderer.load_resolved_servers(cli_name)
        names = ", ".join(sorted(servers)) if servers else t("none")
        lazy = renderer.list_lazy_servers(cli_name)
        lazy_part = t(" — optional, not mounted: {names}", names=", ".join(lazy)) if lazy else ""
        lines.append(f"  {cli_name}: {len(servers)} — {names}{lazy_part}")

    lines.append("\n" + t(">>> Skills: manifest compared against the materialized library"))
    mat = SkillMaterializer(vault_data=effective_vault, engine_root=effective_engine, home=effective_home)
    declared = mat.load_manifest()
    materialized = [p.name for p in mat.library_dir.iterdir() if p.is_dir()] if mat.library_dir.is_dir() else []
    extras = sorted(m for m in materialized if m not in declared)
    missing = sorted(s for s in declared if s not in materialized)
    lines.append(t("  {materialized} materialized, {declared} declared", materialized=len(materialized), declared=len(declared)))
    if extras:
        lines.append(t("  outside the manifest (kept, never deleted): {names}", names=", ".join(extras)))
    if missing:
        lines.append(t("  declared but not materialized yet: {names}", names=", ".join(missing)))
        problems += 1

    lines.append("\n" + t(">>> Instructions per runtime"))
    for label, path, kind in _bootstrap_targets(effective_home):
        state = _instruction_state(path, kind, effective_vault)
        lines.append(f"  {label}: {state}")
        if state.startswith("diverged"):
            problems += 1

    lines.append("\n" + t(">>> Runtimes' native memories"))
    for label, note in _native_memory_report(effective_home):
        lines.append(f"  {label}: {note}")

    lines.append("\n" + t("Read-only: nothing was modified."))
    return lines, (2 if problems else 0)


def render_inventory(
    home: Path | None = None,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
) -> tuple[str, int]:
    """The inventory screen as a single text, plus its exit code."""
    lines, code = inventory_lines(home=home, vault_data=vault_data, engine_root=engine_root)
    return "\n".join(lines), code
