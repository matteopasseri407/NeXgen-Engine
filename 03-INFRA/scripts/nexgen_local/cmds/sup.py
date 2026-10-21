"""Domain commands: supervised tasks. The folder, the visible window, the costs.

Each verb does one step and prints what a person needs to act: where the task
lives, which harness session ran which unit, what it cost. The heavy lifting
stays in `supervision_state`, `supervision_windows` and `supervision_costs`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import supervision_costs as costs
from .. import supervision_gates as gates
from .. import supervision_harness as harness
from .. import supervision_publish as publish_module
from .. import supervision_review as review
from .. import supervision_requests as requests
from .. import supervision_spend as spend
from .. import supervision_stall as stall
from .. import supervision_state as state
from .. import supervision_windows as windows
from .. import supervision_worktree as worktree
from ..supervision_state import SupervisionError
from . import sup_permissions
from . import sup_rebase
from . import sup_cleanup
from . import sup_resume
from . import sup_scout


def register_sup(sub) -> None:
    """Wire the `sup` verbs where the commands live, so `cli.main` stops growing."""
    sup = sub.add_parser("sup", help="compiti supervisionati: cartella, finestre visibili, costi")
    sup_sub = sup.add_subparsers(dest="sup_command", required=True)

    start = sup_sub.add_parser("start", help="crea la cartella del compito e i modelli di brief e resoconto")
    start.add_argument("objective", help="obiettivo del compito, in una riga")
    start.set_defaults(func=cmd_sup_start)

    status = sup_sub.add_parser("status", help="stato di un compito (senza id: elenco dei compiti)")
    status.add_argument("id", nargs="?", default="")
    status.set_defaults(func=cmd_sup_status)

    attach = sup_sub.add_parser("attach", help="aggancia una sessione harness a un'unità e scrive la riga di costo")
    attach.add_argument("id")
    attach.add_argument("unit")
    attach.add_argument("--role", default="esecutore", help="supervisore, esecutore, scout o tutor")
    attach.add_argument("--opencode-session", required=True, help="identificativo della sessione harness")
    attach.add_argument("--window", default="", help="titolo della finestra in cui gira la sessione")
    attach.set_defaults(func=cmd_sup_attach)

    costi = sup_sub.add_parser(
        "costi", help="spesa delle sessioni nelle ultime 5 ore, 7 e 30 giorni; con un id, anche i costi del compito",
    )
    costi.add_argument("id", nargs="?", default="", help="id del compito (senza: solo la spesa delle sessioni)")
    costi.set_defaults(func=cmd_sup_costi)

    check = sup_sub.add_parser("window-check", help="apre una finestra visibile che resta aperta")
    check.set_defaults(func=cmd_sup_window_check)

    unit = sup_sub.add_parser("unit", help="crea la copia di lavoro dell'unità, il ramo locale e il suo confine")
    unit.add_argument("id", help="id del compito")
    unit.add_argument("unit", help="nome dell'unità")
    unit.add_argument("--repo", required=True, help="checkout da cui creare la copia di lavoro")
    unit.add_argument("--base", required=True, help="ref da cui parte il ramo dell'unità")
    unit.add_argument("--brief", required=True, help="file del brief da copiare nella cartella del compito")
    unit.add_argument("--allow", action="append", default=[], help="glob di un percorso consentito (ripetibile)")
    unit.add_argument("--verify", default="", help="comando di verifica che il motore rieseguirà")
    unit.set_defaults(func=cmd_sup_unit)

    run = sup_sub.add_parser("run", help="apre l'esecutore nella sua finestra e aggancia la sua sessione")
    run.add_argument("id", help="id del compito")
    run.add_argument("unit", help="nome dell'unità")
    run.set_defaults(func=cmd_sup_run)

    collect = sup_sub.add_parser("collect", help="esegue i cancelli del motore e scrive il pacchetto di revisione")
    collect.add_argument("id", help="id del compito")
    collect.add_argument("unit", help="nome dell'unità")
    collect.set_defaults(func=cmd_sup_collect)

    _register_review(sup_sub)
    _register_publish(sup_sub)
    sup_permissions.register(sup_sub)
    sup_rebase.register(sup_sub)
    sup_resume.register(sup_sub)
    sup_scout.register(sup_sub)
    sup_cleanup.register(sup_sub)


def _register_review(sup_sub) -> None:
    """The supervisor's verb: one round, either APPROVA or RIMANDA with corrections."""
    review_verb = sup_sub.add_parser("review", help="il giro di revisione: approva l'unità o rimandala con le correzioni")
    review_verb.add_argument("id", help="id del compito")
    review_verb.add_argument("unit", help="nome dell'unità")
    choice = review_verb.add_mutually_exclusive_group(required=True)
    choice.add_argument("--approve", metavar="MOTIVO", default=None, help="approva l'unità raccolta e scrivi il perché")
    choice.add_argument("--rework", metavar="FILE", default=None, help="manda le correzioni all'esecutore nella sua sessione")
    choice.add_argument("--resend", action="store_true", help="riconsegna l'ultimo giro non ancora consegnato")
    review_verb.add_argument(
        "-m", "--model", default="",
        help="con --resend: il modello di questa consegna, quando il provider rifiuta quello configurato",
    )
    review_verb.set_defaults(func=cmd_sup_review)


def _register_publish(sup_sub) -> None:
    """The publishing verb: replay the approved commits, then optionally push forward once."""
    publish = sup_sub.add_parser("publish", help="applica i commit approvati sul ramo del proprietario, mai con forza")
    publish.add_argument("id", help="id del compito")
    publish.add_argument("unit", help="nome dell'unità")
    publish.add_argument("--repo", required=True, help="checkout del proprietario su cui applicare i commit")
    publish.add_argument("--branch", required=True, help="ramo del checkout su cui applicare i commit")
    publish.add_argument("--remote", default="origin", help="remoto da interrogare e su cui spingere")
    how = publish.add_mutually_exclusive_group()
    how.add_argument("--push", action="store_true", help="spinge il ramo in avanti dopo il cherry-pick")
    how.add_argument("--push-with", metavar="COMANDO", default="", help="spinge con il comando dichiarato dal proprietario")
    publish.set_defaults(func=cmd_sup_publish)


def cmd_sup_start(args: argparse.Namespace) -> int:
    """Create the task folder and say where it is."""
    try:
        started = state.start(args.objective)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"compito {started['id']}")
    print(f"cartella {started['path']}")
    print(f"brief e resoconto: copia {state.BRIEF_TEMPLATE} e {state.REPORT_TEMPLATE}")
    return 0


def cmd_sup_status(args: argparse.Namespace) -> int:
    """Print one task's state, or the list of tasks when no id is given."""
    if not args.id:
        return _list_tasks()
    try:
        data = state.status(args.id)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    stalls, stall_problem = _stalls(args.id)
    data = state.status(args.id)
    print(f"compito {data['id']}")
    print(f"obiettivo: {data['objective'] or '(non leggibile)'}")
    print(f"cartella: {data['path']}")
    if data["units"]:
        for unit in data["units"]:
            print(_unit_line(unit))
            note = review.status_note(args.id, unit.get("unit") or "")
            if note:
                print(note)
    else:
        print("unità: nessuna agganciata")
    for item in stalls:
        print(stall.line(item))
    if stalls:
        print(f"per rispondere a una richiesta di permesso: `sup permessi {args.id}`")
    if stall_problem:
        print(f"stallo: non verificabile ({stall_problem})")
    print("sessioni harness: " + (", ".join(data["sessions"]) or "nessuna"))
    last = data["last_event"] or {}
    print("ultimo evento: " + (f"{last.get('event', '')} {last.get('at', '')}".strip() or "nessuno"))
    print("costi: " + _cost_line(data))
    return 0


def _stalls(task_id: str) -> tuple[list[dict], str]:
    """The stalls found now and, when the check cannot run, the one line saying why."""
    try:
        return stall.check(task_id), ""
    except SupervisionError as exc:
        return [], str(exc)


def cmd_sup_attach(args: argparse.Namespace) -> int:
    """Bind one harness session to a unit and write its cost row.

    The row comes first: it validates the id and the unit, so a refused attach
    leaves neither a journal event nor a cost line behind.
    """
    try:
        row = costs.record(
            args.id, args.unit, args.role, args.opencode_session, window=args.window,
        )
        state.append_event(
            args.id, "attached",
            unit=args.unit, role=args.role, harness="opencode", session=row["session"],
        )
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"agganciata {row['unit']} · {row['role']} · {row['harness']} {row['session']}")
    cost = row.get("cost_usd")
    if isinstance(cost, (int, float)):
        print(f"costo {cost:.4f} $, {row.get('tokens_in') or 0} token in, {row.get('tokens_out') or 0} token out")
    else:
        print(f"costo non disponibile: {row.get('note', '')}")
    return 0


def cmd_sup_costi(args: argparse.Namespace) -> int:
    """Print the spend windows, then the task's cost rows by role and by unit when an id is given."""
    if args.id:
        try:
            data = costs.aggregate(args.id)
        except SupervisionError as exc:
            print(f"nexgen-local: {exc}", file=sys.stderr)
            return 1
        print(_format_aggregate(data))
    print("\n".join(_spend_lines(spend.spend_windows())))
    return 0


def cmd_sup_window_check(args: argparse.Namespace) -> int:
    """Open one visible window that runs an innocuous command and stays open."""
    command = [sys.executable, "-c", "print('finestra del supervisore OK')"]
    try:
        handle = windows.open_window("supervisore · prova finestra", command, cwd=Path.cwd())
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"finestra aperta con {handle['launcher']} (pid {handle['pid']})")
    print("esegue: " + " ".join(command))
    print("resta aperta: chiudila quando hai letto l'esito")
    return 0


def cmd_sup_unit(args: argparse.Namespace) -> int:
    """Prepare the unit's working copy and print what the executor will work in."""
    try:
        record = worktree.create(
            args.id, args.unit,
            repo=args.repo, base=args.base, brief=args.brief,
            allow=args.allow, verify=args.verify,
        )
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"unità {record['unit']} · ramo {record['branch']}")
    print(f"copia {record['worktree']}")
    print(f"base {record['base_ref']} ({record['base'][:12]})")
    print("confine: " + ", ".join(record["allow"]))
    print("verifica: " + (record["verify"] or "nessuna dichiarata"))
    broken = [name for name, refs in record["remotes"].items() if isinstance(refs, dict) and refs.get("error")]
    remotes = ", ".join(record["remotes"]) or "nessuno"
    print(f"remoti: {remotes} · push bloccata in questa copia"
          + (f" · non leggibili: {', '.join(broken)}" if broken else ""))
    return 0


def cmd_sup_run(args: argparse.Namespace) -> int:
    """Send the unit into the task's own session, or open the first tab, or queue it."""
    try:
        result = harness.run_unit(args.id, args.unit)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if result["mode"] == "refused":
        return _print_refusal(result)
    if result["mode"] == "queued":
        print(f"unità {result['unit']} in coda: {result['reason']} sulla sessione {result['session']}")
        print("riprova `sup run` quando quella sessione è ferma: una finestra nuova non si apre")
        return 0
    if result["mode"] == "run":
        print(f"lavoro inviato alla sessione {result['session']} (pid {result['pid']}), nessuna scheda nuova")
        row = result["row"]
        print(f"costo all'invio: {row['cost_usd']:.4f} $" if isinstance(row.get("cost_usd"), (int, float))
              else "costo all'invio: non disponibile")
        return 0
    handle = result["window"]
    print(f"finestra {handle.get('launcher')} (pid {handle.get('pid')}) · scheda «{handle.get('title')}» "
          f"dell'unità {result['unit']}")
    session = result["session"]
    if session is None:
        print("sessione non determinata: agganciala a mano con "
              f"`sup attach {args.id} {result['unit']} --opencode-session <ses_...>`")
        return 0
    print(f"sessione agganciata: {session.get('id')} · {session.get('title', '')}")
    return 0


def cmd_sup_collect(args: argparse.Namespace) -> int:
    """Run the engine's gates on the unit and print the verdict, line by line."""
    try:
        result = gates.collect(args.id, args.unit)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"unità {result['unit']} · verdetto {result['verdict']}")
    for gate in result["gates"]:
        print(f"{'PASS' if gate.ok else 'FAIL'} {gate.name}: {gate.reason}")
    print(f"pacchetto {result['package']}")
    print(f"grezzo {result['raw']}")
    for hit in result.get("requests") or []:
        print(f"richiesta al proprietario: {_request_line(hit)}")
    if result.get("request_section"):
        print(f"richieste: {result['request_section']}")
    return 0 if result["verdict"] == "PASS" else 1


def cmd_sup_review(args: argparse.Namespace) -> int:
    """Approve the collected unit or send the corrections into its own session."""
    if args.model and not args.resend:
        print("nexgen-local: `-m` vale solo con `--resend`", file=sys.stderr)
        return 1
    try:
        if args.approve is not None:
            result = review.approve(args.id, args.unit, args.approve)
        elif args.resend:
            result = review.resend(args.id, args.unit, model=args.model)
        else:
            result = review.rework(args.id, args.unit, args.rework)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"unità {result['unit']} · giro {result['round']} · {result['verdict']}")
    print(f"revisione {result['path']}")
    print(f"head {result['head'][:12]}")
    if result["verdict"] == state.APPROVED:
        print(f"raccolta {result['collect_verdict']} sulla stessa head: pronta per `sup publish`")
    else:
        print(_delivery_line(result["delivery"]))
    return 0


def cmd_sup_publish(args: argparse.Namespace) -> int:
    """Replay the approved unit on the owner's branch and say what happened to the push."""
    try:
        result = publish_module.publish(
            args.id, args.unit, repo=args.repo, branch=args.branch, remote=args.remote,
            push=args.push, push_with=args.push_with,
        )
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if result.get("replayed", True):
        print(f"unità {result['unit']} · {len(result['commits'])} commit su {result['branch']} in {result['repo']}")
    else:
        print(f"unità {result['unit']} · già applicata in locale, {len(result['commits'])} commit "
              f"pubblicati su {result['branch']} in {result['repo']}")
    for commit in result["commits"]:
        print(f"{commit['published'][:12]} <- {commit['source'][:12]} · {commit['subject']}")
    print(result["signing"])
    print(windows.close_tab(args.id, "esecutore")["line"])
    if result["pushed"]:
        print(f"spinta eseguita: {result['command']}")
        return 0
    if result["command"]:
        print(f"pubblicazione dichiarata non riuscita: `{result['command']}` è fallito")
        print(_tail(result["output"]))
        return 1
    print("nessuna spinta: i commit sono sul ramo locale, il remoto non è stato toccato")
    print(f"resta da fare: `git push {result['remote']} {result['branch']}` dal checkout, oppure "
          "`sup publish ... --push` (o `--push-with \"<comando>\"`)")
    return 0


def _print_refusal(result: dict) -> int:
    """One refused run: name the model, say whether any work was sent, and exit 1."""
    session = result.get("session")
    session_id = str(session.get("id") if isinstance(session, dict) else session or "")
    print(f"unità {result['unit']} rifiutata: {result['reason']}")
    if result.get("sent"):
        print(f"sessione {session_id}: la scheda si è appena aperta e il brief le è già arrivato; "
              "chiudila, apri la sessione su un modello consentito e riprova")
    else:
        print(f"sessione {session_id}: nessun lavoro inviato; cambia il modello della sessione "
              "o l'elenco in config.json, poi riprova")
    return 1


def _delivery_line(delivery: dict) -> str:
    """Where the corrections went, and whether the owner's model reached them."""
    if delivery["mode"] == "queued":
        line = f"correzioni in coda: {delivery['reason']} sulla sessione {delivery['session']}"
    elif delivery["mode"] == "run":
        line = f"correzioni inviate alla sessione {delivery['session']} (pid {delivery['pid']})"
    else:
        line = f"finestra aperta per le correzioni sulla sessione {delivery.get('session')}"
    if delivery.get("model"):
        line += f" · modello {delivery['model']}"
    elif delivery.get("model_requested"):
        line += (
            f" · modello {delivery['model_requested']} non applicato: una scheda nuova "
            "prende il modello della configurazione del proprietario"
        )
    return line


def _request_line(hit: dict) -> str:
    """One request the engine will not execute, as it goes to the owner's file."""
    return f"{hit['kind']} · «{hit['sentence']}» (scritta in {requests.FILE})"


def _tail(text: str, limit: int = 1200) -> str:
    """The end of a command's output, where the reason of a failure lives."""
    return (text or "").strip()[-limit:]


def _list_tasks() -> int:
    """Every task folder, one line each: the id is what every other verb wants."""
    print("\n".join(state.tasks_lines()))
    return 0


def _unit_line(unit: dict) -> str:
    """One unit as the owner reads it: who ran it, how far it got, the review, the cost."""
    parts = [f"unità {unit.get('unit', '')}"]
    if unit.get("role"):
        parts.append(str(unit["role"]))
    session = f"{unit.get('harness', '')} {unit.get('session', '')}".strip()
    if session:
        parts.append(session)
    if unit.get("state"):
        parts.append(str(unit["state"]))
    if unit.get("verdict"):
        parts.append(str(unit["verdict"]))
    parts.append(f"giro {unit.get('review_round') or 0}")
    parts.append("approvata" if unit.get("approved") else "non approvata")
    if unit.get("published"):
        head = str(unit.get("published_head") or "")[:8]
        where = str(unit.get("published_branch") or "")
        pushed = "spinta" if unit.get("published_pushed") else "solo locale"
        parts.append(f"pubblicata {head} su {where} ({pushed})")
    cost = unit.get("cost_usd")
    parts.append(f"{cost:.4f} $" if isinstance(cost, (int, float)) else "costo non disponibile")
    return " · ".join(parts)


def _cost_line(data: dict) -> str:
    """The total with its unknowns said out loud, never a zero nobody measured."""
    total = data.get("cost_total_usd")
    rows = data.get("cost_rows") or 0
    unknown = data.get("cost_unknown_rows") or 0
    if not rows:
        return "nessuna riga (nessuna sessione agganciata)"
    if total is None:
        return f"non disponibile ({_row_count(rows)}, {unknown} senza fonte)"
    missing = f", {unknown} senza fonte" if unknown else ""
    return f"{total:.4f} $ ({_row_count(rows)}{missing})"


def _format_aggregate(data: dict) -> str:
    """The aggregate as a short block: task total, then role and unit lines."""
    lines = [_bucket_line("compito " + str(data.get("task", "")), data)]
    for title, key in (("ruoli", "by_role"), ("unità", "by_unit")):
        groups = data.get(key) or {}
        if not groups:
            lines.append(f"{title}: nessuna riga")
            continue
        lines.append(f"{title}:")
        for name, bucket in sorted(groups.items()):
            lines.append("  " + _bucket_line(name, bucket))
    return "\n".join(lines)


#: How each spend window is printed, in the order the owner reads them.
SPEND_LABELS = (("5h", "ultime 5 ore"), ("7d", "ultimi 7 giorni"), ("30d", "ultimi 30 giorni"))


def _spend_lines(data: dict) -> list[str]:
    """The three windows, or one line saying why the archive would not answer."""
    if data["note"]:
        return [f"spesa delle sessioni: non disponibile ({data['note']})"]
    lines = [f"spesa delle sessioni ({data['source']}, figlie comprese) alle {data['read_at']}:"]
    for key, label in SPEND_LABELS:
        window = data["windows"][key]
        lines.append(f"  {label}: {window['cost_usd']:.4f} $ ({_session_count(window['sessions'])})")
    return lines


def _session_count(count: int) -> str:
    """One session, two sessions: a window counts sessions, not rows of the register."""
    return f"{count} sessione" if count == 1 else f"{count} sessioni"


def _bucket_line(name: str, bucket: dict) -> str:
    """One aggregate line: rows, money or its absence, unknowns, tokens."""
    cost = bucket.get("cost_usd")
    money = f"{cost:.4f} $" if isinstance(cost, (int, float)) else "costo non disponibile"
    unknown = bucket.get("unknown_rows") or 0
    unknown_text = f", {unknown} senza fonte" if unknown else ""
    return (
        f"{name}: {_row_count(bucket.get('rows', 0))}, {money}{unknown_text}, "
        f"{bucket.get('tokens_in', 0)} token in, {bucket.get('tokens_out', 0)} token out"
    )


def _row_count(count: int) -> str:
    """One row, two rows: the owner reads these lines."""
    return f"{count} riga" if count == 1 else f"{count} righe"
