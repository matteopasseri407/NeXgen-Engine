# nexgen: one-job reason="declare the sup verbs and print what each one did"
"""Domain commands: supervised tasks. The folder, the visible window, the costs.

Each verb does one step and prints what a person needs to act: where the task
lives, which harness session ran which unit, what it cost. The heavy lifting
stays in `supervision_state`, `supervision_windows` and `supervision_costs`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import supervision_costs as costs
from .. import supervision_cleanup as cleanup
from .. import supervision_gates as gates
from .. import supervision_adapters as adapters
from .. import supervision_harness as harness
from .. import supervision_policy as policy
from .. import supervision_publish as publish_module
from .. import supervision_review as review
from .. import supervision_requests as requests
from .. import supervision_service as service
from .. import supervision_spend as spend
from .. import supervision_stall as stall
from .. import supervision_state as state
from ..supervision_status import row_count, task_line, unit_line
from .. import supervision_windows as windows
from .. import supervision_workboard as workboard
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
    status.add_argument("--json", action="store_true", help="le stesse righe, in JSON, per chi le deve inoltrare")
    status.set_defaults(func=cmd_sup_status)

    attach = sup_sub.add_parser("attach", help="aggancia una sessione harness a un'unità e scrive la riga di costo")
    _register_attach(attach)

    costi = sup_sub.add_parser(
        "costi", help="spesa delle sessioni nelle ultime 5 ore, 7 e 30 giorni; con un id, anche i costi del compito",
    )
    costi.add_argument("id", nargs="?", default="", help="id del compito (senza: solo la spesa delle sessioni)")
    costi.add_argument("--json", action="store_true", help="le stesse righe, in JSON, per chi le deve inoltrare")
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
    unit.add_argument("--mcp", action="append", default=[], help="server MCP consentito all'esecutore (ripetibile, default: nessuno)")
    unit.set_defaults(func=cmd_sup_unit)

    run = sup_sub.add_parser("run", help="apre l'esecutore nella sua finestra e aggancia la sua sessione")
    _register_run(run)

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


def _register_attach(attach) -> None:
    """The attach verb: one harness session, its role, its harness and its window."""
    attach.add_argument("id")
    attach.add_argument("unit")
    attach.add_argument("--role", default="esecutore", help="supervisore, esecutore, scout o tutor")
    attach.add_argument(
        "--opencode-session", "--session", dest="opencode_session", required=True,
        help="identificativo della sessione harness (`--session` è lo stesso)",
    )
    attach.add_argument("--harness", default="", help="harness della sessione (default: opencode)")
    attach.add_argument("--window", default="", help="titolo della finestra in cui gira la sessione")
    attach.set_defaults(func=cmd_sup_attach)


def _register_run(run) -> None:
    """The run verb: which unit, which harness, and the model of the tab it opens."""
    run.add_argument("id", help="id del compito")
    run.add_argument("unit", help="nome dell'unità")
    run.add_argument(
        "--harness", default="",
        help="harness di questa esecuzione (default: quello del ruolo esecutore nella lane)",
    )
    run.add_argument(
        "-m", "--model", default="",
        help="modello di questa scheda, quando l'harness lo accetta sulla riga di comando (agy)",
    )
    run.set_defaults(func=cmd_sup_run)


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
    review_verb.add_argument(
        "--close", action="store_true",
        help="con --approve: toglie copia e ramo di un'unità che non si pubblica, tenendone la head in un archivio",
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
        return _emit(state.tasks_lines(), args)
    try:
        data = state.status(args.id)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    stalls, stall_problem = _stalls(args.id)
    lines = [f"compito {data['id']}"]
    lines.append(f"obiettivo: {data['objective'] or '(non leggibile)'}")
    lines.append(f"cartella: {data['path']}")
    roles_line = _roles_line()
    if roles_line:
        lines.append(roles_line)
    local_line = _local_line()
    if local_line:
        lines.append(local_line)
    if data["units"]:
        for unit in data["units"]:
            lines.append(unit_line(unit))
            note = review.status_note(args.id, unit.get("unit") or "")
            if note:
                lines.append(note)
    else:
        lines.append("unità: nessuna agganciata")
    for item in stalls:
        lines.append(stall.line(item))
    if stalls:
        lines.append(f"per rispondere a una richiesta di permesso: `sup permessi {args.id}`")
    if stall_problem:
        lines.append(f"stallo: non verificabile ({stall_problem})")
    lines.append("sessioni harness: " + (", ".join(data["sessions"]) or "nessuna"))
    last = data["last_event"] or {}
    lines.append("ultimo evento: " + (f"{last.get('event', '')} {last.get('at', '')}".strip() or "nessuno"))
    lines.append(task_line(data))
    return _emit(lines, args)


def _stalls(task_id: str) -> tuple[list[dict], str]:
    """The stalls found now and, when the check cannot run, the one line saying why."""
    try:
        return stall.check(task_id), ""
    except SupervisionError as exc:
        return [], str(exc)


def _roles_line() -> str:
    """Which harness takes each role: the lane's table, or the defaults it did not override."""
    try:
        chosen = policy.roles()
    except SupervisionError as exc:
        return f"ruoli: non leggibili ({exc})"
    return "ruoli: " + " · ".join(f"{role} {name}" for role, name in chosen.items())


def _local_line() -> str:
    """The local server's state, printed only when the lane declares a local model."""
    try:
        declared = policy.local_model()
    except SupervisionError as exc:
        return f"modello locale: configurazione non leggibile ({exc})"
    if not declared:
        return ""
    from .. import supervision_local_model as local

    return local.describe()


def cmd_sup_attach(args: argparse.Namespace) -> int:
    """Bind one harness session to a unit and write its cost row.

    The row comes first: it validates the id and the unit, so a refused attach
    leaves neither a journal event nor a cost line behind.
    """
    try:
        chosen = args.harness or "opencode"
        row = costs.record(
            args.id, args.unit, args.role, args.opencode_session, harness=chosen, window=args.window,
        )
        state.append_event(
            args.id, "attached",
            unit=args.unit, role=args.role, harness=chosen, session=row["session"],
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
    lines: list[str] = []
    if args.id:
        try:
            data = costs.aggregate(args.id)
        except SupervisionError as exc:
            print(f"nexgen-local: {exc}", file=sys.stderr)
            return 1
        lines.append(_format_aggregate(data))
    lines.extend(_spend_lines(spend.spend_windows()))
    return _emit(lines, args)


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
            allow=args.allow, verify=args.verify, mcp=args.mcp,
        )
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"unità {record['unit']} · ramo {record['branch']}")
    print(f"copia {record['worktree']}")
    print(f"base {record['base_ref']} ({record['base'][:12]})")
    print("confine: " + ", ".join(record["allow"]))
    print("verifica: " + (record["verify"] or "nessuna dichiarata"))
    mcp_assigned = record.get("mcp") or []
    print("mcp: " + (", ".join(mcp_assigned) if mcp_assigned else "nessuno"))
    broken = [name for name, refs in record["remotes"].items() if isinstance(refs, dict) and refs.get("error")]
    remotes = ", ".join(record["remotes"]) or "nessuno"
    print(f"remoti: {remotes} · push bloccata in questa copia"
          + (f" · non leggibili: {', '.join(broken)}" if broken else ""))
    _board_card(args.id, record["unit"], record)
    return 0


def cmd_sup_run(args: argparse.Namespace) -> int:
    """Send the unit into the task's own session, or open the first tab, or queue it."""
    try:
        result = _run_unit(args)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    for note in result.get("notes") or []:
        print(f"nota: {note}")
    if result.get("replaced_tab"):
        print(result["replaced_tab"]["line"])
    if result["mode"] == "refused":
        return _print_refusal(result)
    if result["mode"] == "queued":
        print(f"unità {result['unit']} in coda: {result['reason']} sulla sessione {result['session']}")
        print("riprova `sup run` quando quella sessione è ferma: una finestra nuova non si apre")
        return 0
    if result["mode"] == "run":
        print(f"lavoro inviato alla sessione {result['session']} (pid {result['pid']}), nessuna scheda nuova")
        if result.get("reason"):
            print(f"nota: {result['reason']}")
        row = result.get("row") or {}
        print(f"costo all'invio: {row['cost_usd']:.4f} $" if isinstance(row.get("cost_usd"), (int, float))
              else "costo all'invio: non disponibile")
        _print_model(result)
        _board("running", args.id, result["unit"])
        return 0
    handle = result["window"]
    print(f"finestra {handle.get('launcher')} (pid {handle.get('pid')}) · scheda «{handle.get('title')}» "
          f"dell'unità {result['unit']}")
    if result.get("reason"):
        print(f"perché una scheda nuova: {result['reason']}")
    _print_model(result)
    _board("running", args.id, result["unit"])
    session = result["session"]
    if session is None:
        print("sessione non determinata: agganciala a mano con "
              f"`sup attach {args.id} {result['unit']} --session <id>`")
        return 0
    print(f"sessione agganciata: {session.get('id')} · {session.get('title', '')}")
    return 0


def _print_model(result: dict) -> None:
    """Say which model this tab took, and when the harness took none, say that instead."""
    requested = result.get("model_requested")
    if not requested:
        return
    if result.get("model_applied"):
        print(f"modello della scheda: {requested}")
    else:
        print(f"modello {requested} non applicato: questo harness sceglie il modello nella sua configurazione")


def _run_unit(args: argparse.Namespace) -> dict:
    """Call the engine with only the keywords the owner actually gave.

    The callers and the tests replace `run_unit` with a two-argument function; a
    keyword nobody asked for would make that replacement unreachable.
    """
    options: dict[str, str] = {}
    if getattr(args, "harness", ""):
        options["harness"] = args.harness
    if getattr(args, "model", ""):
        options["model"] = args.model
    return harness.run_unit(args.id, args.unit, **options)


def cmd_sup_collect(args: argparse.Namespace) -> int:
    """Run the engine's gates on the unit, print the verdict and the cost, then close its tab."""
    try:
        early_close = _close_stopped_tab(args.id, args.unit)
        result = gates.collect(args.id, args.unit)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"unità {result['unit']} · verdetto {result['verdict']}")
    for gate in result["gates"]:
        print(f"{'PASS' if gate.ok else 'FAIL'} {gate.name}: {gate.reason}")
    if result.get("report_note"):
        print(f"nota: {result['report_note']}")
    print(costs.cost_text(result["cost"]))
    print(f"pacchetto {result['package']}")
    print(f"grezzo {result['raw']}")
    print(_collect_tab_line(args.id, result["unit"], result["cost"]) or early_close)
    for note in result.get("local_notes") or []:
        print(f"nota: {note}")
    for note in cleanup.notes():
        print(f"nota: {note}")
    for hit in result.get("requests") or []:
        print(f"richiesta al proprietario: {_request_line(hit)}")
    if result.get("request_section"):
        print(f"richieste: {result['request_section']}")
    _board("review", args.id, result["unit"])
    return 0 if result["verdict"] == "PASS" else 1


def _close_stopped_tab(task_id: str, unit: str) -> str:
    """Close the executor's tab before the gates read its report, and say what happened.

    A sandboxed harness can keep its files unreadable to the engine while its tab
    is alive: Codex on Windows writes the report as a separate sandbox user and
    releases it only when the tab exits. The session's own state says whether the
    turn is over; only then is the tab closed, and a task with another unit still
    working keeps it open. A harness with no tab of its own keeps its own session:
    nothing of the engine holds those files, so there is nothing to close here.
    """
    record = state.unit_record(task_id, unit)
    name = str(record.get("unit") or unit)
    chosen = str(record.get("harness") or "opencode")
    if _window_less(chosen):
        return ""
    price = costs.collect_cost(task_id, name, harness=chosen)
    if not price.stopped:
        return ""
    closed = windows.close_tab(task_id, "esecutore", still_working=(name,), stopped=True, unit=name)
    return str(closed.get("line") or "")


def _collect_tab_line(task_id: str, unit: str, cost: costs.CollectCost) -> str:
    """The collection ends the work, so its tab goes; a session still writing keeps it."""
    if not cost.stopped:
        why = "non misurata" if cost.note else "ancora al lavoro"
        return f"sessione {why}: la scheda resta aperta"
    if _window_less(str(state.unit_record(task_id, unit).get("harness") or "opencode")):
        return "la sessione resta nel Control UI del harness, non in una scheda del motore"
    closed = windows.close_tab(task_id, "esecutore", still_working=(unit,), stopped=True, unit=unit)
    return closed["line"]


def cmd_sup_review(args: argparse.Namespace) -> int:
    """Approve the collected unit or send the corrections into its own session."""
    if args.model and not args.resend:
        print("nexgen-local: `-m` vale solo con `--resend`", file=sys.stderr)
        return 1
    if getattr(args, "close", False) and args.approve is None:
        print("nexgen-local: `--close` vale solo con `--approve`", file=sys.stderr)
        return 1
    try:
        if args.approve is not None:
            result = review.approve(args.id, args.unit, args.approve)
            closing = review.close(args.id, args.unit) if args.close else None
        elif args.resend:
            result, closing = review.resend(args.id, args.unit, model=args.model), None
        else:
            result, closing = review.rework(args.id, args.unit, args.rework), None
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"unità {result['unit']} · giro {result['round']} · {result['verdict']}")
    print(f"revisione {result['path']}")
    print(f"head {result['head'][:12]}")
    if result["verdict"] == state.APPROVED:
        print(f"raccolta {result['collect_verdict']} sulla stessa head: pronta per `sup publish`")
        _print_close(closing)
        if closing and closing.get("retired"):
            print(_close_work(args.id, result["unit"]))
            print("\n".join(_after_work(args.id, result["unit"])))
            _board("blocked", args.id, result["unit"], reason="approvata e chiusa senza pubblicazione")
    else:
        print(_delivery_line(result["delivery"]))
        _board("running", args.id, result["unit"])
    return 0


def _print_close(closing: dict | None) -> None:
    """What closing an unpublished unit did: the archive that keeps it, and what went."""
    if not closing:
        return
    if not closing.get("retired"):
        print(f"unità non ritirata: {closing.get('reason', '')}")
        return
    print(f"unità chiusa senza pubblicazione · copia {closing['copy']} · ramo {closing['branch']}")
    print(f"archivio {closing['archive'] or 'nessuno'} · head conservata {closing['head'][:12]}")
    for note in closing.get("notes") or []:
        print(f"nota: {note}")


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
    print(_close_work(args.id, result["unit"]))
    _print_retirement(result.get("retirement"))
    print("\n".join(_after_work(args.id, result["unit"])))
    if result["pushed"]:
        print(f"spinta eseguita: {result['command']}")
        _board("done", args.id, result["unit"])
        return 0
    if result["command"]:
        print(f"pubblicazione dichiarata non riuscita: `{result['command']}` è fallito")
        print(_tail(result["output"]))
        return 1
    print("nessuna spinta: i commit sono sul ramo locale, il remoto non è stato toccato")
    print(f"resta da fare: `git push {result['remote']} {result['branch']}` dal checkout, oppure "
          "`sup publish ... --push` (o `--push-with \"<comando>\"`)")
    _board("done", args.id, result["unit"])
    return 0


def _print_retirement(retirement: dict | None) -> None:
    """Say whether the copy and the branch of the published unit are gone, or why they stayed."""
    if not retirement:
        return
    if retirement.get("retired"):
        print(f"unità ritirata: copia {retirement['copy']} e ramo {retirement['branch']} rimossi")
        for note in retirement.get("notes") or []:
            print(f"nota: {note}")
        return
    print(f"unità non ritirata: {retirement.get('reason') or retirement.get('error', '')}")


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
    session = delivery.get("session")
    session_id = str(session.get("id") if isinstance(session, dict) else session or "")
    if delivery["mode"] == "queued":
        line = f"correzioni in coda: {delivery['reason']} sulla sessione {session_id}"
    elif delivery["mode"] == "run":
        line = f"correzioni inviate alla sessione {session_id} (pid {delivery['pid']})"
    else:
        line = f"finestra aperta per le correzioni sulla sessione {session_id}"
    if delivery.get("replaced_tab"):
        line += f" · {delivery['replaced_tab']['line']}"
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


def _emit(lines: list[str], args: argparse.Namespace) -> int:
    """Print the lines the verb built, or the same lines inside the JSON a caller forwards.

    The words are built once: a chat, a tool and a terminal read the same sentence, and
    `--json` only decides how it travels.
    """
    if getattr(args, "json", False):
        print(json.dumps({"lines": lines}, ensure_ascii=False))
        return 0
    print("\n".join(lines))
    return 0


def _board(status: str, task_id: str, unit: str, *, reason: str = "") -> None:
    """Move the unit's card and print the one line the board has to say, if it has one.

    The journal is the state and the verb has already done its work: a board that
    is off, missing or refusing costs a line here and nothing else.
    """
    try:
        line = workboard.move(task_id, unit, status, reason=reason)
    except SupervisionError as exc:
        line = f"scheda Workboard non spostata: {exc}"
    if line:
        print(line)


def _board_card(task_id: str, unit: str, record: dict) -> None:
    """Create the unit's card and print the one line the board has to say, if it has one."""
    try:
        line = workboard.create(task_id, unit, record)
    except SupervisionError as exc:
        line = f"scheda Workboard non creata: {exc}"
    if line:
        print(line)


def _window_less(harness_name: str) -> bool:
    """Whether this harness keeps its own sessions instead of a tab of the engine."""
    try:
        return not adapters.get(harness_name).capabilities.window
    except SupervisionError:  # nexgen: allow reason="un nome che il registro non conosce non ha una finestra da chiudere: la chiusura resta quella delle schede, che è il comportamento di prima"
        return False


def _after_work(task_id: str, unit: str) -> list[str]:
    """The pass the brief asks for at the end of every publication and every close.

    Two different leavings: the tab goes with the unit where the work ended, and the
    shared server's children born before the oldest live session go here. A machine
    that ends a task with a thousand orphan processes is not finished with anything,
    and the owner should not have to type `sup pulizia` to get his memory back.
    A collection that cannot run is a note: the publication already landed, and
    nothing here may take it back.
    """
    try:
        collected = service.collect()
    except SupervisionError as exc:  # nexgen: allow reason="la pubblicazione è già avvenuta: una raccolta che non parte è una nota, non un errore"
        return [f"processi del server condiviso non raccolti: {exc}"]
    lines = service.lines(collected)
    if collected.get("closed"):
        try:
            state.append_event(
                task_id, "service_collected", unit=unit,
                pids=list(collected["closed"]), since=collected.get("since") or "",
            )
        except SupervisionError as exc:  # nexgen: allow reason="un diario che non si scrive non annulla una raccolta già fatta né una pubblicazione già avvenuta"
            lines.append(f"  raccolta non scritta nel diario: {exc}")
    return lines


def _close_work(task_id: str, unit: str) -> str:
    """Close what the executor left open: the tab of a window harness, the session of the other.

    Publishing ends the unit's work, so the thing the owner was watching can go:
    the tab is closed by the window layer, and a harness that shows its own
    sessions has that session archived instead.
    """
    record = state.unit_record(task_id, unit)
    name = str(record.get("unit") or unit)
    chosen = str(record.get("harness") or "opencode")
    if not _window_less(chosen):
        return windows.close_tab(task_id, "esecutore", still_working=(name,), stopped=True, unit=name)["line"]
    session = str(record.get("session") or state.harness_session(task_id, chosen))
    if not session:
        return "nessuna sessione da chiudere"
    line = adapters.get(chosen).close_session(session)
    state.append_event(task_id, "session_closed", unit=name, harness=chosen, session=session, line=line)
    return line


def _list_tasks() -> int:
    """Every task folder, one line each: the id is what every other verb wants."""
    print("\n".join(state.tasks_lines()))
    return 0


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
        f"{name}: {row_count(bucket.get('rows', 0))}, {money}{unknown_text}, "
        f"{bucket.get('tokens_in', 0)} token in, {bucket.get('tokens_out', 0)} token out"
    )
