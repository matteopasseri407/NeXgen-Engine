"""I verbi che agiscono sul motore: allineare, diagnosticare, aggiornare.

Ogni funzione qui riceve gli argomenti già analizzati e restituisce un exit
code. Nessuna decide come formattare: la formattazione è una scelta presa una
volta sola al bordo, in `__init__.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from nexgen_core.paths import resolve_engine_root, resolve_vault_data


def _all(args) -> list[str]:
    """Gli argomenti che il dispatcher non ha riconosciuto, nel loro ordine."""
    return list(getattr(args, "passthrough", []) or [])


def register(sub) -> None:
    """Dichiara i verbi del motore sul dispatcher."""
    p = sub.add_parser("sync", aliases=["apply"], help="Allinea questa macchina adesso")
    p.add_argument("--offline", "--allow-offline", dest="allow_offline", action="store_true",
                   help="Procedi anche senza raggiungere il remoto")
    p.add_argument("--skip-mcp", action="store_true",
                   help="Non rigenerare le configurazioni dei connettori")
    p.set_defaults(func=cmd_sync, mode="apply")

    p = sub.add_parser("guard", help="Il ciclo ricorrente di allineamento (non pubblica mai)")
    p.add_argument("--offline", "--allow-offline", dest="allow_offline", action="store_true")
    p.add_argument("--skip-mcp", action="store_true")
    p.set_defaults(func=cmd_sync, mode="guard")

    p = sub.add_parser("pull", help="Scarica i dati senza rigenerare i file derivati")
    p.add_argument("--offline", "--allow-offline", dest="allow_offline", action="store_true")
    p.set_defaults(func=cmd_sync, mode="pull", skip_mcp=False)

    p = sub.add_parser("preflight", help="Controlla la configurazione senza scrivere niente")
    p.set_defaults(func=cmd_sync, mode="preflight", allow_offline=False, skip_mcp=False)

    p = sub.add_parser("doctor", help="Dimmi se qualcosa non va")
    p.add_argument("-v", "--verbose", action="store_true", help="Elenca tutto ciò che è stato controllato")
    p.add_argument("--strict", action="store_true", help="Tratta come guasto anche ciò che non si può determinare")
    p.add_argument("--json", action="store_true", help="Output in formato JSON")
    p.add_argument("--summary", action="store_true", help="Una riga di riepilogo")
    p.add_argument("--fix", "--remedy", dest="fix", action="store_true",
                   help="Applica i rimedi automatici disponibili")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("update", help="Aggiorna il motore, con conferma")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("upgrades", help="Mostrami cosa si è mosso a monte")
    p.set_defaults(func=cmd_upgrades)

    p = sub.add_parser("inventory", help="Cosa c'è installato su questa macchina, senza toccare niente")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("config", help="Mostra la configurazione dei remoti risolta")
    p.add_argument("field", choices=["authoritative_remote", "mirrors"])
    p.set_defaults(func=cmd_config)

    # Verbi interni: li invocano i timer, non le persone.
    p = sub.add_parser("heartbeat", help="Battito di liveness e manutenzioni (uso interno)")
    p.set_defaults(func=cmd_heartbeat)

    p = sub.add_parser("notify-failure", help="Allarme per un'unità di guardia non partita (uso interno)")
    p.add_argument("unit", nargs="?", default="un'unità di guardia")
    p.set_defaults(func=cmd_notify_failure)

    p = sub.add_parser("bootstrap-alerts", help="Diagnostica e allerta solo sui guasti (uso interno)")
    p.set_defaults(func=cmd_bootstrap_alerts)


def cmd_sync(args) -> int:
    from nexgen_core.guard import GuardMode, GuardRunner

    runner = GuardRunner()
    res = runner.run(
        mode=GuardMode(args.mode),
        allow_offline=getattr(args, "allow_offline", False),
        skip_mcp=getattr(args, "skip_mcp", False),
    )
    for act in res.actions_taken:
        print(f"  ✓ {act}")
    print(res.message)
    return res.exit_code


def cmd_doctor(args) -> int:
    from nexgen_core.doctor import Doctor

    report = Doctor().run_diagnostics(apply_remedies=args.fix)
    if args.json:
        print(report.format_json())
    elif args.summary:
        print(f"FAIL={len(report.broken)} OK={report.ok_count} UNDETERMINED={len(report.undetermined)}")
    else:
        print(report.format_human(verbose=args.verbose))
    return report.exit_code(strict=args.strict)


def cmd_update(args) -> int:
    from nexgen_core.updater import main as updater_main

    return updater_main(_all(args))


def cmd_upgrades(args) -> int:
    from nexgen_core.updater import EngineUpdater

    return EngineUpdater.main(["--check"])


def cmd_config(args) -> int:
    from nexgen_core.git_ops import resolve_remotes

    auth_remote, mirrors = resolve_remotes(resolve_vault_data())
    print(auth_remote if args.field == "authoritative_remote" else "\n".join(mirrors))
    return 0


def cmd_heartbeat(args) -> int:
    from nexgen_core.beat import Heartbeat

    res = Heartbeat().run_beat()
    status = "attivo" if res["liveness_ok"] else "fermo"
    print(f"Battito: {status} ({res['liveness_msg']})")
    return 0 if res["liveness_ok"] else 1


def cmd_notify_failure(args) -> int:
    from nexgen_core.megaphone import Megaphone

    unit = args.unit
    summary = (
        f"{unit} non è partita, e finché non parte questa macchina smette di allinearsi. "
        f"Controllala con: systemctl --user status {unit}"
    )
    if not Megaphone().send_alert(
        title="La guardia non è partita",
        message=summary,
        action=f"systemctl --user status {unit}",
        alert_key=f"notify-failure-{unit}",
    ):
        print(f"notify-failure: {summary} (nessun canale di allarme configurato)", file=sys.stderr)
    return 0


def cmd_bootstrap_alerts(args) -> int:
    from nexgen_core.doctor import Doctor
    from nexgen_core.megaphone import Megaphone

    report = Doctor().run_diagnostics(apply_remedies=False)
    if report.has_failures:
        Megaphone().send_alert(
            title="Ci sono problemi su questa macchina",
            message="; ".join(o.message for o in report.broken[:5]),
            action="nexgen doctor",
            alert_key="bootstrap-alerts-doctor",
        )
    print(f"Diagnostica completata (guasti={len(report.broken)}, a posto={report.ok_count}).")
    return report.exit_code()


def cmd_inventory(args) -> int:
    """Cosa c'è davvero su questa macchina, confrontato con cosa dovrebbe esserci."""
    from nexgen_core.renderer import McpRenderer
    from nexgen_core.skills import SkillMaterializer

    home = Path.home()
    vault_data = resolve_vault_data(home)
    engine_root = resolve_engine_root(home)
    problems = 0

    print(">>> Connettori MCP per runtime")
    renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
    for cli_name in ("claude", "codex", "antigravity", "opencode"):
        servers = renderer.load_resolved_servers(cli_name)
        names = ", ".join(sorted(servers)) if servers else "nessuno"
        print(f"  {cli_name}: {len(servers)} — {names}")

    print("\n>>> Skill: manifest a confronto con la libreria materializzata")
    mat = SkillMaterializer(vault_data=vault_data, engine_root=engine_root, home=home)
    declared = mat.load_manifest()
    materialized = [p.name for p in mat.library_dir.iterdir() if p.is_dir()] if mat.library_dir.is_dir() else []
    extras = sorted(m for m in materialized if m not in declared)
    missing = sorted(s for s in declared if s not in materialized)
    print(f"  {len(materialized)} materializzate, {len(declared)} dichiarate")
    if extras:
        print(f"  fuori manifest (conservate, mai cancellate): {', '.join(extras)}")
    if missing:
        print(f"  dichiarate ma non ancora materializzate: {', '.join(missing)}")
        problems += 1

    print("\n>>> Istruzioni per runtime")
    for label, path, kind in _bootstrap_targets(home):
        state = _instruction_state(path, kind, vault_data)
        print(f"  {label}: {state}")
        if state.startswith("divergente"):
            problems += 1

    print("\n>>> Memorie native dei runtime")
    for label, note in _native_memory_report(home):
        print(f"  {label}: {note}")

    print("\nSola lettura: niente è stato modificato.")
    return 2 if problems else 0


def _bootstrap_targets(home: Path) -> list[tuple[str, Path, str]]:
    return [
        ("claude", home / "CLAUDE.md", "pointer"),
        ("codex", home / ".codex" / "AGENTS.md", "mirror"),
        ("antigravity", home / ".gemini" / "config" / "AGENTS.md", "mirror"),
    ]


def _instruction_state(path: Path, kind: str, vault_data: Path) -> str:
    """Dice non solo se il file c'è, ma se dice ancora la verità.

    "Presente" non basta: una copia reale che ha smesso di combaciare con il
    canonico è esattamente il caso che questo inventario deve scoprire.
    """
    import hashlib

    from nexgen_core.paths import canonical_instructions

    canon = canonical_instructions(vault_data)
    if not path.exists() and not path.is_symlink():
        return "assente"
    if path.is_symlink():
        return f"collegamento -> {path.resolve()}"
    try:
        body = path.read_bytes()
    except OSError as exc:
        return f"illeggibile ({exc})"
    if kind == "pointer":
        return "puntatore" if str(canon) in body.decode("utf-8", "replace") else "divergente: non nomina il canonico"
    if not canon.is_file():
        return "copia reale (il canonico non esiste, impossibile confrontare)"
    same = hashlib.sha256(body).hexdigest() == hashlib.sha256(canon.read_bytes()).hexdigest()
    return "copia allineata" if same else "divergente: copia reale diversa dal canonico"


def _native_memory_report(home: Path) -> list[tuple[str, str]]:
    """Le memorie che ogni runtime tiene per conto suo, accanto al Vault.

    Non è una diagnosi: è un censimento, perché l'onboarding deve poter dire
    all'utente cosa esiste già prima di proporgli di adottarlo o azzerarlo.
    """
    out: list[tuple[str, str]] = []

    claude_memory = home / ".claude" / "memory"
    if claude_memory.is_dir():
        facts = sum(1 for _ in claude_memory.rglob("*.md"))
        out.append(("claude", f"{facts} fatti durevoli in {claude_memory}"))
    else:
        out.append(("claude", "nessuna memoria nativa"))

    for label, path in (
        ("codex", home / ".codex" / "sessions"),
        ("opencode", home / ".opencode" / "storage"),
        ("antigravity", home / ".gemini" / "tmp"),
    ):
        if path.is_dir():
            count = sum(1 for _ in path.rglob("*") if _.is_file())
            out.append((label, f"{count} file di trascrizione in {path} (da distillare, non memoria strutturata)"))
        else:
            out.append((label, "nessuna trascrizione"))

    return out
