#!/usr/bin/env python3
"""Il primo avvio: cosa manca, cosa c'è, e qual è il passo dopo.

Questa logica esisteva due volte, in `install.sh` e in `install.ps1`, tenute
in passo a mano. Erano già in deriva: uno dei due avvisava che jq serve anche
agli script di salute, l'altro no. Ora esiste una volta sola, e i due
installer sono gusci che trovano Python e passano il testimone qui.

Non sostituisce l'installazione guidata da un agente: fa la parte meccanica.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: La versione di Python sotto la quale il motore non parte.
MINIMUM_PYTHON = (3, 11)

#: Le cartelle che un vault deve avere. Mancanti, si creano.
SCAFFOLD_DIRS = ("01-NOTES", "02-PROJECTS", "04-NOW", "99-INDEX", "99-SECRETS")

#: I file senza i quali il clone non è completo. Mancanti, si dice e basta.
SCAFFOLD_FILES = (
    "INIT.md",
    "00-START-HERE.md",
    "99-INDEX/USER-PROFILE.md",
    "03-INFRA/agent-universal-layer/instructions/AGENTS.md",
)


@dataclass(frozen=True)
class Finding:
    """Un esito del preflight: cosa si è guardato, com'è andata, e il rimedio."""

    label: str
    ok: bool
    required: bool = True
    remedy: str = ""


def _colour(stream) -> dict[str, str]:
    """Colori solo se qualcuno li può vedere."""
    if not stream.isatty() or os.environ.get("NO_COLOR"):
        return dict.fromkeys(("bold", "dim", "reset", "green", "red", "yellow", "cyan"), "")
    return {
        "bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m",
        "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m", "cyan": "\033[36m",
    }


def package_hint() -> str:
    """Come si installa un pacchetto su questo sistema."""
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return "brew install"
        return "installa Homebrew (https://brew.sh), poi: brew install"
    if system == "Windows":
        return "winget install"
    for manager, command in (("apt", "sudo apt install"), ("dnf", "sudo dnf install"),
                             ("pacman", "sudo pacman -S"), ("zypper", "sudo zypper install")):
        if shutil.which(manager):
            return command
    return "il gestore di pacchetti del tuo sistema"


def preflight() -> list[Finding]:
    """Cosa serve, e cosa c'è davvero."""
    hint = package_hint()
    found: list[Finding] = []

    found.append(Finding("git", shutil.which("git") is not None, True, f"{hint} git"))

    version_ok = sys.version_info >= MINIMUM_PYTHON
    wanted = ".".join(str(n) for n in MINIMUM_PYTHON)
    found.append(Finding(
        f"Python {platform.python_version()}", version_ok, True,
        f"serve Python {wanted} o successivo → {hint} python3",
    ))

    has_yaml = importlib.util.find_spec("yaml") is not None
    found.append(Finding("PyYAML", has_yaml, True, "pip install pyyaml"))

    # Il resto è utile ma non blocca: dirlo come blocco fa reinstallare cose
    # a chi non ne ha bisogno.
    found.append(Finding(
        "node/npx", shutil.which("npx") is not None, False,
        "serve solo per montare connettori MCP o skill installate da npx",
    ))
    found.append(Finding(
        "gpg", shutil.which("gpg") is not None, False,
        "serve solo se tieni segreti cifrati in 99-SECRETS/",
    ))
    found.append(Finding(
        "docker", shutil.which("docker") is not None, False,
        "serve solo per l'installazione completa su questa macchina (nexgen stack up)",
    ))
    return found


def scaffold(root: Path, write: bool) -> list[Finding]:
    """Le cartelle e i file che un vault deve avere."""
    found: list[Finding] = []
    for name in SCAFFOLD_DIRS:
        target = root / name
        if target.is_dir():
            found.append(Finding(f"{name}/", True))
            continue
        if write:
            target.mkdir(parents=True, exist_ok=True)
            (target / ".gitkeep").touch()
            found.append(Finding(f"{name}/ (creata)", True))
        else:
            found.append(Finding(f"{name}/", False, True, "rilancia senza --check per crearla"))

    for name in SCAFFOLD_FILES:
        found.append(Finding(
            name, (root / name).is_file(), True,
            "il clone sembra incompleto: ricontrolla di aver clonato tutto il repository",
        ))
    return found


def detect_clis(home: Path | None = None) -> list[str]:
    """Quali assistenti da riga di comando ci sono su questa macchina.

    Si guarda il binario, non una cartella: dedurre "installata" da una
    cartella che il layer stesso crea è un difetto già trovato due volte.
    """
    home_dir = home or Path.home()
    found = [name for name in ("claude", "codex", "opencode") if shutil.which(name)]
    if shutil.which("agy") or (home_dir / ".gemini" / "settings.json").is_file():
        found.append("antigravity")
    return found


def install_launchers(root: Path) -> str:
    """Genera i comandi, se il motore vive in questo clone."""
    scripts_dir = root / "03-INFRA" / "scripts"
    if not (scripts_dir / "nexgen_core").is_dir():
        return ""
    sys.path.insert(0, str(scripts_dir))
    try:
        from nexgen_core.shims import install_shims

        installed = install_shims(scripts_dir=scripts_dir)
        return f"{len(installed)} comandi installati in ~/.local/bin"
    except Exception as exc:
        return f"comandi non installati ({exc})"


def _ask(prompt: str, options: str) -> str:
    try:
        return input(f"  {prompt} [{options}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


def guided_profile() -> tuple[str, str]:
    """Due domande, e il profilo che ne consegue. Non scrive niente."""
    clis = _ask("Quante CLI userai?", "1 / 2+")
    machines = _ask("Quante macchine vuoi tenere allineate?", "1 / 2+")
    arch = _ask("Dove vivono i servizi?", "N=nessuno / Q=qui / S=su un server")

    profile = "MULTI" if ("2" in clis or "2" in machines) else "MINIMAL"
    mode = {"q": "LOCAL-FULL", "s": "CLOUD-SERVER"}.get(arch[:1], "LOCAL-ONLY")
    return profile, mode


def render(findings: list[Finding], title: str, stream=sys.stdout) -> int:
    """Stampa un blocco di esiti e restituisce quanti requisiti mancano."""
    c = _colour(stream)
    print(f"\n{c['bold']}{c['cyan']}{title}{c['reset']}", file=stream)
    missing = 0
    for f in findings:
        if f.ok:
            print(f"  {c['green']}✓{c['reset']} {f.label}", file=stream)
        elif f.required:
            print(f"  {c['red']}✗{c['reset']} {f.label} — {f.remedy}", file=stream)
            missing += 1
        else:
            print(f"  {c['yellow']}○{c['reset']} {f.label} — {f.remedy}", file=stream)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-bootstrap",
        description="Controlla i prerequisiti, prepara il vault e dice qual è il passo dopo.",
    )
    parser.add_argument("--check", action="store_true",
                        help="Solo controlli: nessuna domanda e nessuna scrittura")
    parser.add_argument("--root", default=None, help="Radice del vault (default: la cartella del repository)")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[3]
    c = _colour(sys.stdout)

    print(f"{c['bold']}{c['cyan']}NeXgen Engine · primo avvio{c['reset']}")
    print(f"{c['dim']}Un vault in Git per la configurazione e la memoria dei tuoi assistenti.{c['reset']}")

    missing = render(preflight(), "1 · Prerequisiti")
    missing += render(scaffold(root, write=not args.check), "2 · Struttura del vault")

    clis = detect_clis()
    found = [Finding(name, True) for name in clis] or [Finding(
        "nessuna CLI trovata", False, True,
        "serve un assistente che sappia scrivere file (Claude Code, Codex, OpenCode, Antigravity): "
        "una chat web non può farlo",
    )]
    missing += render(found, "3 · Assistenti trovati su questa macchina")

    if missing:
        print(f"\n{c['red']}Manca qualcosa di necessario.{c['reset']} Sistemalo e rilancia.")
        return 1

    if not args.check and sys.stdin.isatty():
        print(f"\n{c['bold']}{c['cyan']}4 · Che installazione vuoi{c['reset']}")
        print(f"  {c['dim']}Nessun file viene scritto: è solo un consiglio.{c['reset']}")
        profile, mode = guided_profile()
        print(f"\n  Profilo: {c['bold']}{profile}{c['reset']} · Servizi: {c['bold']}{mode}{c['reset']}")
        if mode == "LOCAL-FULL":
            print(f"  {c['dim']}→ i cinque connettori girano qui: 'nexgen stack up' li avvia.{c['reset']}")
        elif mode == "CLOUD-SERVER":
            print(f"  {c['dim']}→ i servizi stanno su un server: vedi 03-INFRA/deploy/.{c['reset']}")
        else:
            print(f"  {c['dim']}→ nessun servizio: ricerca nativa, niente automazioni remote.{c['reset']}")

    print(f"\n{c['bold']}{c['cyan']}5 · Passo successivo{c['reset']}")
    # `--check` dichiara di non scrivere niente, quindi non scrive niente.
    # La versione precedente installava comunque i comandi da qui, il che
    # rendeva falsa la sua stessa promessa e, su un clone di sviluppo,
    # dirottava i comandi della macchina verso quel clone.
    if not args.check:
        note = install_launchers(root)
        if note:
            print(f"  {c['green']}✓{c['reset']} {note}")
    print(f"""
  Apri {c['bold']}INIT.md{c['reset']} e incollane il contenuto in un assistente da riga di
  comando aperto in questa cartella. Ti farà qualche domanda e monterà
  connettori e skill.

  {c['dim']}Poi, per verificare in qualunque momento: nexgen doctor{c['reset']}""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
