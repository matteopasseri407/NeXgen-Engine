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
    canonical_instructions,
    mcp_manifest,
    resolve_engine_root,
    resolve_home,
    resolve_vault_data,
    skills_manifest,
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
    except Exception:
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
    except Exception:
        doctor_status = "not evaluated"

    # Notes count
    notes_count = 0
    if vault.is_dir():
        try:
            notes_count = len(list(vault.rglob("*.md")))
        except OSError:
            pass

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
    }


def render_info(as_json: bool = False, vault_data: Path | None = None) -> str:
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

    # Section 3: Security & Health
    out.append(f"  {C_EMERALD}{C_BOLD}SECURITY & DIAGNOSTICS{C_RESET}")
    out.append(f"  {C_SLATE}• Secrets Store:{C_RESET} {info['secrets']}")
    out.append(f"  {C_SLATE}• Health/Doctor:{C_RESET} {C_GREEN}● {info['doctor']}{C_RESET}")
    out.append("")
    out.append(f"  {C_DARK_SLATE}{'─' * 74}{C_RESET}")
    out.append(f"  {C_DIM}Run 'nexgen doctor' for full diagnosis · 'nexgen modules list' for module matrix{C_RESET}")
    out.append("")

    return "\n".join(out)
