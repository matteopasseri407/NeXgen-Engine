#!/usr/bin/env python3
"""CLI dispatcher principale per i comandi di NeXgen Engine v2."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.beat import Heartbeat
from nexgen_core.doctor import Doctor
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

    return 0


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
