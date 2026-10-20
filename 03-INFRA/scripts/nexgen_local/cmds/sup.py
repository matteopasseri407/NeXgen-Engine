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
from .. import supervision_state as state
from .. import supervision_windows as windows
from ..supervision_state import SupervisionError


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

    costi = sup_sub.add_parser("costi", help="costi del compito, per ruolo e per unità")
    costi.add_argument("id")
    costi.set_defaults(func=cmd_sup_costi)

    check = sup_sub.add_parser("window-check", help="apre una finestra visibile che resta aperta")
    check.set_defaults(func=cmd_sup_window_check)


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
    print(f"compito {data['id']}")
    print(f"obiettivo: {data['objective'] or '(non leggibile)'}")
    print(f"cartella: {data['path']}")
    if data["units"]:
        for unit in data["units"]:
            print(f"unità {unit['unit']} · {unit['role']} · {unit['harness']} {unit['session']}")
    else:
        print("unità: nessuna agganciata")
    print("sessioni harness: " + (", ".join(data["sessions"]) or "nessuna"))
    last = data["last_event"] or {}
    print("ultimo evento: " + (f"{last.get('event', '')} {last.get('at', '')}".strip() or "nessuno"))
    print("costi: " + _cost_line(data))
    return 0


def cmd_sup_attach(args: argparse.Namespace) -> int:
    """Bind one harness session to a unit and write its cost row."""
    try:
        state.append_event(
            args.id, "attached",
            unit=args.unit, role=args.role, harness="opencode", session=args.opencode_session,
        )
        row = costs.record(
            args.id, args.unit, args.role, args.opencode_session, window=args.window,
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
    """Print the task's cost rows summed by role and by unit."""
    try:
        data = costs.aggregate(args.id)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(_format_aggregate(data))
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


def _list_tasks() -> int:
    """Every task folder, one line each: the id is what every other verb wants."""
    tasks = state.list_tasks()
    if not tasks:
        print("nessun compito: `sup start \"<obiettivo>\"`")
        return 0
    for task in tasks:
        print(f"{task['id']}  {task['objective'] or '(obiettivo non leggibile)'}")
    return 0


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
