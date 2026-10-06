"""Command line for the optional local lane: dispatcher only.

Each domain lives in nexgen_local.cmds (run/patch/mail/cal/wf/drive/relay/service);
this module keeps argument wiring (main) and re-exports every cmd_* so
`from nexgen_local import cli` and `cli.cmd_mail_propose` keep working.
"""
from __future__ import annotations

import argparse

from .cmds import base as _base
from .cmds.cal import cmd_cal_apply, cmd_cal_propose, cmd_cals
from .cmds.drive import cmd_drive_mcp, cmd_drive_propose, cmd_drive_upload
from .cmds.mail import cmd_mail_propose, cmd_mail_send, cmd_mails
from .cmds.patch_cmds import cmd_apply, cmd_proposals, cmd_propose
from .cmds.relay_cmd import cmd_relay
from .cmds.run import cmd_close, cmd_eval, cmd_explore, cmd_research, cmd_run
from .cmds.service import cmd_doctor, cmd_mcp
from .cmds.wf import cmd_wf_propose, cmd_wf_run, cmd_wfs
from .relay import RELAY_CLIS


def _version() -> str:
    return _base.version_str()


_config = _base.get_config
_llm = _base.make_llm


def _result_payload(result):
    return _base.result_payload(result)


def _warn_unverified(problems: list[str]) -> None:
    _base.warn_unverified(problems)


def _print_receipts(receipts: list[dict]) -> None:
    _base.print_receipts(receipts)


__all__ = [
    "cmd_run", "cmd_research", "cmd_close", "cmd_explore", "cmd_eval",
    "cmd_propose", "cmd_proposals", "cmd_apply",
    "cmd_mail_propose", "cmd_mails", "cmd_mail_send",
    "cmd_cal_propose", "cmd_cals", "cmd_cal_apply",
    "cmd_wf_propose", "cmd_wfs", "cmd_wf_run",
    "cmd_drive_propose", "cmd_drive_upload", "cmd_drive_mcp",
    "cmd_relay", "cmd_mcp", "cmd_doctor", "main",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-local",
        description="Lane locale governata: sola lettura, tool decisi dal motore, ricevute su audit.",
    )
    parser.add_argument("--version", action="version", version=f"nexgen-local {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="risponde a una domanda attraverso la lane")
    run.add_argument("question")
    run.add_argument("--model")
    run.add_argument("--router-model", help="modello per il routing (default: --model)")
    run.add_argument("--answer-model", help="modello per la risposta (default: --model)")
    run.add_argument("--vault")
    run.add_argument("--repo", action="append")
    run.add_argument("--audit")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    evaluate = sub.add_parser("eval", help="esegue le suite di valutazione")
    evaluate.add_argument("--suite", choices=("capability", "traps", "patch", "jobs", "agent", "all"), default="all")
    evaluate.add_argument("--model")
    evaluate.add_argument("--router-model", help="modello per il routing (default: --model)")
    evaluate.add_argument("--answer-model", help="modello per la risposta (default: --model)")
    evaluate.add_argument("--bare", action="store_true",
                          help="misura il modello da solo: disattiva lo strato del motore che trattiene le istruzioni iniettate")
    evaluate.add_argument("--json", action="store_true")
    evaluate.set_defaults(func=cmd_eval)

    research = sub.add_parser("research", help="cerca su vault e web e sintetizza con citazioni")
    research.add_argument("topic")
    research.add_argument("--model")
    research.add_argument("--answer-model", help="modello per la sintesi (default: --model)")
    research.add_argument("--vault")
    research.add_argument("--repo", action="append")
    research.add_argument("--audit")
    research.add_argument("--json", action="store_true")
    research.set_defaults(func=cmd_research)

    close = sub.add_parser("close", help="estrae gli esiti durevoli di una sessione in una bozza")
    close.add_argument("--file", required=True)
    close.add_argument("--save", action="store_true", help="salva la bozza nella cartella di stato della lane")
    close.add_argument("--model")
    close.add_argument("--router-model", help="modello per l'estrazione (default: --model)")
    close.add_argument("--vault")
    close.add_argument("--repo", action="append")
    close.add_argument("--audit")
    close.add_argument("--json", action="store_true")
    close.set_defaults(func=cmd_close)

    explore = sub.add_parser("explore", help="loop agentico limitato: il modello sceglie l'azione dal menu del motore")
    explore.add_argument("task")
    explore.add_argument("--max-steps", type=int, default=6)
    explore.add_argument("--session-id", default="", help="continua la sessione di ricerca (vuoto: effimero, 'new': nuova persistente)")
    explore.add_argument("--model")
    explore.add_argument("--router-model")
    explore.add_argument("--answer-model")
    explore.add_argument("--vault")
    explore.add_argument("--repo", action="append")
    explore.add_argument("--audit")
    explore.add_argument("--json", action="store_true")
    explore.set_defaults(func=cmd_explore)

    mcp = sub.add_parser("mcp", help="server MCP stdio: la lane come servizio per gli agenti")
    mcp.add_argument("--jobs-only", action="store_true", help="nascondi lane_ask; restano research, close, status ed explore")
    mcp.add_argument("--model")
    mcp.add_argument("--router-model")
    mcp.add_argument("--answer-model")
    mcp.add_argument("--vault")
    mcp.add_argument("--repo", action="append")
    mcp.add_argument("--audit")
    mcp.set_defaults(func=cmd_mcp)

    propose = sub.add_parser("propose", help="propone una patch (cancello a fatti macchina)")
    propose.add_argument("--file", required=True)
    propose.add_argument("--instruction", required=True)
    propose.add_argument("--model")
    propose.add_argument("--repo", action="append")
    propose.add_argument("--json", action="store_true")
    propose.set_defaults(func=cmd_propose)

    proposals = sub.add_parser("proposals", help="elenca le proposte")
    proposals.add_argument("--json", action="store_true")
    proposals.set_defaults(func=cmd_proposals)

    apply_cmd = sub.add_parser("apply", help="applica una proposta (serve --yes)")
    apply_cmd.add_argument("proposal_id")
    apply_cmd.add_argument("--yes", action="store_true")
    apply_cmd.add_argument("--repo", action="append", help="root del repository (deve combaciare con quello approvato)")
    apply_cmd.add_argument("--verify", default="", help="comando di verifica dopo l'applicazione; exit 3 se fallisce")
    apply_cmd.add_argument("--json", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply)

    mail_propose = sub.add_parser("mail-propose", help="bozza una mail (il corpo lo scrive il modello, la busta il motore)")
    mail_propose.add_argument("--instruction", required=True, help="cosa deve dire la mail")
    mail_propose.add_argument("--reply", default="", help="id del messaggio a cui rispondere")
    mail_propose.add_argument("--to", default="", help="destinatario per un nuovo messaggio")
    mail_propose.add_argument("--subject", default="", help="oggetto per un nuovo messaggio")
    mail_propose.add_argument("--provider", default="gmail", choices=("gmail", "outlook"))
    mail_propose.add_argument("--model")
    mail_propose.add_argument("--json", action="store_true")
    mail_propose.set_defaults(func=cmd_mail_propose)

    mails = sub.add_parser("mails", help="elenca le bozze mail da approvare")
    mails.add_argument("--json", action="store_true")
    mails.set_defaults(func=cmd_mails)

    mail_send = sub.add_parser("mail-send", help="invia una bozza approvata (serve --yes)")
    mail_send.add_argument("proposal_id")
    mail_send.add_argument("--yes", action="store_true")
    mail_send.add_argument("--json", action="store_true")
    mail_send.set_defaults(func=cmd_mail_send)

    cal_propose = sub.add_parser("cal-propose", help="propone un evento o un'eliminazione (cancello)")
    cal_propose.add_argument("--summary", default="", help="titolo dell'evento")
    cal_propose.add_argument("--start", default="", help="inizio ISO 8601 con timezone")
    cal_propose.add_argument("--end", default="", help="fine ISO 8601 con timezone")
    cal_propose.add_argument("--description", default="")
    cal_propose.add_argument("--location", default="")
    cal_propose.add_argument("--calendar", default="primary")
    cal_propose.add_argument("--delete", default="", help="id evento da eliminare (invece di creare)")
    cal_propose.add_argument("--json", action="store_true")
    cal_propose.set_defaults(func=cmd_cal_propose)

    cals = sub.add_parser("cals", help="elenca le proposte calendario da approvare")
    cals.add_argument("--json", action="store_true")
    cals.set_defaults(func=cmd_cals)

    cal_apply = sub.add_parser("cal-apply", help="applica una proposta calendario (serve --yes)")
    cal_apply.add_argument("proposal_id")
    cal_apply.add_argument("--yes", action="store_true")
    cal_apply.add_argument("--json", action="store_true")
    cal_apply.set_defaults(func=cmd_cal_apply)

    wf_propose = sub.add_parser("wf-propose", help="prepara l'esecuzione di un workflow consentito")
    wf_propose.add_argument("--workflow", required=True, help="nome in allowlist")
    wf_propose.add_argument("--params", default="", help="oggetto JSON per il webhook")
    wf_propose.add_argument("--json", action="store_true")
    wf_propose.set_defaults(func=cmd_wf_propose)

    wfs = sub.add_parser("wfs", help="elenca workflow consentiti e proposte")
    wfs.add_argument("--json", action="store_true")
    wfs.set_defaults(func=cmd_wfs)

    wf_run = sub.add_parser("wf-run", help="esegue una proposta approvata (serve --yes)")
    wf_run.add_argument("proposal_id")
    wf_run.add_argument("--yes", action="store_true")
    wf_run.add_argument("--json", action="store_true")
    wf_run.set_defaults(func=cmd_wf_run)

    drive_propose = sub.add_parser("drive-propose", help="prepara un caricamento su Drive (dentro vault/repo)")
    drive_propose.add_argument("--file", required=True, help="file locale da caricare")
    drive_propose.add_argument("--name", default="", help="nome su Drive (default: nome file)")
    drive_propose.add_argument("--folder", default="", help="id cartella di destinazione")
    drive_propose.add_argument("--json", action="store_true")
    drive_propose.set_defaults(func=cmd_drive_propose)

    drive_upload = sub.add_parser("drive-upload", help="carica una proposta approvata (serve --yes)")
    drive_upload.add_argument("proposal_id")
    drive_upload.add_argument("--yes", action="store_true")
    drive_upload.add_argument("--json", action="store_true")
    drive_upload.set_defaults(func=cmd_drive_upload)

    drive_mcp = sub.add_parser("drive-mcp", help="server MCP stdio: Drive per tutti gli agenti")
    drive_mcp.add_argument("--vault")
    drive_mcp.add_argument("--repo", action="append")
    drive_mcp.add_argument("--audit")
    drive_mcp.set_defaults(func=cmd_drive_mcp)

    relay = sub.add_parser("relay", help="passa una domanda a un'altra CLI (sola lettura, isolata)")
    relay.add_argument("--list", action="store_true", help="mostra i CLI supportati e installati")
    relay.add_argument("--cli", choices=RELAY_CLIS)
    relay.add_argument("--model")
    relay.add_argument("--prompt")
    relay.add_argument("--file", help="allega un file di testo al prompt (dentro vault/repo)")
    relay.add_argument(
        "--allow-outside-attach",
        action="store_true",
        help="consenti un allegato fuori dalle radici consentite (esplicito)",
    )
    relay.add_argument("--timeout", type=int, default=600)
    relay.add_argument("--json", action="store_true")
    relay.set_defaults(func=cmd_relay)

    doctor = sub.add_parser("doctor", help="verifica precondizioni e superficie")
    doctor.add_argument("--model")
    doctor.add_argument("--vault")
    doctor.add_argument("--audit")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
