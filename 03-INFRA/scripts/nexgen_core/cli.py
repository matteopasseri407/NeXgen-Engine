#!/usr/bin/env python3
"""CLI dispatcher principale per i comandi di NeXgen Engine v2."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.beat import Heartbeat
from nexgen_core.doctor import Doctor
from nexgen_core.git_ops import resolve_remotes
from nexgen_core.guard import GuardMode, GuardRunner
from nexgen_core.publisher import Publisher
from nexgen_core.updater import EngineUpdater


def _run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-sync",
        description="NeXgen Engine (v2) - Sincronizzazione e gestione del layer agentico"
    )

    subparsers = parser.add_subparsers(dest="command", help="Comando da eseguire")

    # guard
    sub_guard = subparsers.add_parser("guard", help="Ciclo ricorrente di guardia e allineamento (non pubblica mai)")
    sub_guard.add_argument("--allow-offline", action="store_true", help="Consente l'esecuzione offline")

    # apply
    sub_apply = subparsers.add_parser("apply", help="Allinea e applica manualmente le configurazioni")
    sub_apply.add_argument("--allow-offline", action="store_true", help="Consente l'esecuzione offline")
    sub_apply.add_argument("--require-ready", action="store_true", help="Richiede che tutti i controlli siano strettamente READY")

    # pull
    sub_pull = subparsers.add_parser("pull", help="Scarica i dati dal remoto senza rigenerare i file runtime")

    # preflight
    sub_pf = subparsers.add_parser("preflight", help="Valida la configurazione in sola lettura")

    # publish / vault-push
    sub_pub = subparsers.add_parser("publish", help="Pubblica i commit del Vault verso il remoto autoritativo")
    sub_pub.add_argument("-m", "--message", default="update: vault sync", help="Messaggio di commit")
    sub_pub.add_argument("files", nargs="*", help="File specifici da pubblicare")

    # doctor
    sub_doc = subparsers.add_parser("doctor", help="Esegue la diagnostica dello stato di salute")
    sub_doc.add_argument("-v", "--verbose", action="store_true", help="Mostra i controlli dettagliati")
    sub_doc.add_argument("--strict", action="store_true", help="Modalità rigorosa")
    sub_doc.add_argument("--json", action="store_true", help="Output in formato JSON")
    sub_doc.add_argument("--summary", action="store_true", help="Stampa il riepilogo FAIL=N OK=N")

    # heartbeat
    sub_beat = subparsers.add_parser("heartbeat", help="Esegue il battito di liveness e controllo dipendenze")

    # notify-failure (trigger OnFailure= di systemd)
    sub_nf = subparsers.add_parser("notify-failure", help="Invia un allarme per un'unità di guardia fallita")
    sub_nf.add_argument("unit", nargs="?", default="a guardian unit", help="Nome dell'unità fallita")

    # upgrades
    sub_up = subparsers.add_parser("upgrades", help="Mostra gli aggiornamenti disponibili")

    # config (ispezione remotes risolti)
    sub_cfg = subparsers.add_parser("config", help="Mostra la configurazione dei remote risolta (authoritative_remote|mirrors)")
    sub_cfg.add_argument("field", choices=["authoritative_remote", "mirrors"], help="Campo da mostrare")

    # inventory (scan onboarding read-only)
    sub_inv = subparsers.add_parser("inventory", help="Scan read-only dell'installazione (MCP, skill, bootstrap)")

    # bootstrap-alerts (healthcheck + alert su FAIL; creds alert sono env-based)
    sub_ba = subparsers.add_parser("bootstrap-alerts", help="Esegue la diagnostica e allerta solo su FAIL")

    # council
    sub_council = subparsers.add_parser("council", help="Avvia una sessione del Council multi-modello")
    sub_council.add_argument("council_args", nargs="*", help="Argomenti da passare al Council")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command in ("guard", "apply", "pull", "preflight"):
        mode_map = {
            "guard": GuardMode.GUARD,
            "apply": GuardMode.APPLY,
            "pull": GuardMode.PULL,
            "preflight": GuardMode.PREFLIGHT,
        }
        runner = GuardRunner()
        allow_offline = getattr(args, "allow_offline", False)
        res = runner.run(mode=mode_map[args.command], allow_offline=allow_offline)
        if res.actions_taken:
            for act in res.actions_taken:
                print(f"  ✓ {act}")
        print(res.message)
        return res.exit_code

    elif args.command == "publish":
        pub = Publisher()
        code, msg = pub.publish(message=args.message, files=args.files or None)
        print(msg)
        return code

    elif args.command == "doctor":
        doc = Doctor()
        report = doc.run_diagnostics()
        if args.json:
            print(report.format_json())
        elif getattr(args, "summary", False):
            print(f"FAIL={len(report.broken)} OK={report.ok_count} UNDETERMINED={len(report.undetermined)}")
        else:
            print(report.format_human(verbose=args.verbose))
        return report.exit_code(strict=args.strict)

    elif args.command == "heartbeat":
        beat = Heartbeat()
        res = beat.run_beat()
        status = "OK" if res["liveness_ok"] else "NON ATTIVO"
        print(f"Battito liveness: {status} ({res['liveness_msg']})")
        return 0 if res["liveness_ok"] else 1

    elif args.command == "notify-failure":
        from nexgen_core.megaphone import Megaphone
        unit = args.unit
        summary = (
            f"FAIL {unit} could not run. The layer stops syncing until it does. "
            f"Check it with: systemctl --user status {unit}"
        )
        meg = Megaphone()
        if not meg.send_alert(
            title="Guardia agente non attiva",
            message=summary,
            action=f"Verifica l'unità: systemctl --user status {unit}",
            alert_key=f"notify-failure-{unit}",
        ):
            print(f"notify-failure: {summary} (no transport configured)", file=sys.stderr)
        return 0

    elif args.command == "upgrades":
        return EngineUpdater.main(["--check"])

    elif args.command == "council":
        from nexgen_core.tools.council import main as council_main
        return council_main(args.council_args)

    elif args.command == "config":
        return _config_cli(args.field)

    elif args.command == "inventory":
        return _inventory_cli()

    elif args.command == "bootstrap-alerts":
        return _bootstrap_alerts_cli()

    return 0


def _config_cli(field: str) -> int:
    """Mostra il remote autoritativo o i mirror risolti (read-only)."""
    vault_data = Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(Path.home() / "KnowledgeVault"))
    auth_remote, mirrors = resolve_remotes(Path(vault_data))
    if field == "authoritative_remote":
        print(auth_remote)
    else:
        print("\n".join(mirrors))
    return 0


def _inventory_cli() -> int:
    """Scan read-only dell'installazione: MCP, skill, bootstrap per CLI."""
    from nexgen_core.renderer import McpRenderer
    from nexgen_core.skills import SkillMaterializer

    home = Path.home()
    vault_data = Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(home / "KnowledgeVault"))
    engine_root = Path(os.environ.get("AGENT_ENGINE_ROOT") or str(home / ".nexgen-engine" / "03-INFRA"))

    # MCP per CLI
    print(">>> Onboarding inventory -- MCP servers per CLI (read-only):")
    renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
    for cli in ("claude", "codex", "antigravity", "opencode"):
        servers = renderer.load_resolved_servers(cli)
        print(f"  {cli}: {len(servers)} server -- " + ", ".join(sorted(servers)) if servers else f"  {cli}: 0 server")

    # Skill: manifest vs libreria materializzata
    print("")
    print(">>> Onboarding inventory -- skills (manifest vs materialized library):")
    mat = SkillMaterializer(vault_data=vault_data, engine_root=engine_root, home=home)
    skills = mat.load_manifest()
    materialized = [p.name for p in mat.library_dir.iterdir() if p.is_dir()] if mat.library_dir.is_dir() else []
    extras = [m for m in materialized if m not in skills]
    missing = [s for s in skills if s not in materialized]
    print(f"  {len(materialized)} materialized skill(s) -- {len(materialized) - len(extras)} canonical, {len(extras)} out-of-manifest")
    if extras:
        print(f"      out-of-manifest: {', '.join(extras)}")
    if missing:
        print(f"      in manifest but not materialized: {', '.join(missing)}")

    # Bootstrap per CLI
    print("")
    print(">>> Onboarding inventory -- bootstrap per CLI (read-only):")
    bootstraps = [
        ("claude", home / "CLAUDE.md", "pointer"),
        ("codex", home / ".codex" / "AGENTS.md", "mirror"),
        ("antigravity", home / ".gemini" / "config" / "AGENTS.md", "mirror"),
    ]
    for label, path, _kind in bootstraps:
        if path.is_file():
            print(f"  {label}: presente ({path})")
        else:
            print(f"  {label}: assente")

    print("")
    print(">>> Read-only. Adopt (canonize) or reset what's out-of-manifest via the onboarding flow.")
    return 0


def _bootstrap_alerts_cli() -> int:
    """Healthcheck: diagnostica e allerta solo su FAIL (creds alert sono env-based in v2)."""
    from nexgen_core.megaphone import Megaphone
    from nexgen_core.doctor import Doctor

    meg = Megaphone()
    doc = Doctor()
    report = doc.run_diagnostics(apply_remedies=False)
    if report.has_failures:
        meg.send_alert(
            title="agent-doctor ha rilevato problemi",
            message="; ".join(o.message for o in report.broken[:5]),
            action="Esegui 'agent-doctor' per i dettagli",
            alert_key="bootstrap-alerts-doctor",
        )
    print(f"bootstrap-alerts: diagnostica completata (FAIL={len(report.broken)} OK={report.ok_count}).")
    return report.exit_code()


def main(argv: list[str] | None = None) -> int:
    """Punto di ingresso principale con intercettazione sicura degli errori."""
    if argv is None:
        argv = sys.argv[1:]
    try:
        return _run_cli(argv)
    except KeyboardInterrupt:
        print("\nOperazione interrotta dall'utente.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[ERRORE] agent-sync: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
