#!/usr/bin/env python3
"""AI Council: local orchestrator that convenes advisor CLIs (via flat
subscription, never pay-per-use API) for brainstorming, challenge, and
cross-vendor code review. See the project note for the full architecture.

A2: three modes (multi-round brainstorm, challenge, code-review), dedicated
role prompts, VERDICT parsing per round.

CLI entry point only: argparse wiring and the ``cmd_*`` handlers. The actual
work lives in sibling modules in this directory -- ``seat_process`` (spawn/
stream/timeout one seat), ``session`` (session lifecycle, private files,
shutdown handlers, egress/output privacy gates), ``proposal`` (config/routing
loading and the human-facing proposal), ``relay`` (sequential multi-seat
relay, ephemeral loop), ``relay_graph`` (resumable relay on LangGraph,
same leaves as relay: _select_stage_candidate/_invoke_stage_candidate),
and ``verdict`` (brief construction, round running, VERDICT parsing).

Layout contract: this directory runs as scripts via the launcher
(nexgen_core/tools/council.py → subprocess council.py). Bare
`from proposal import …` is intentional for both layouts. Underscore
names are cross-file internals, not API. Routing decisions: role-based
via routing.resolve_role_candidates, single-seat via proposal.resolve_seat,
consult bypasses routing (relay._validate_relay_seat) — three correct
answers by mode, see each cmd_*.
"""
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

# Council may validate a data-root file directly. That read-only check must
# not leave Python cache files next to the user's data on an error path.
sys.dont_write_bytecode = True

#: This directory (council sources + prompts/), misnamed for history.
#: Repo-root resolution lives in nexgen_core.paths; the installed-layout
#: fallback lives in nexgen_core/tools/council.py. Do not add a third.
COUNCIL_DIR = Path(__file__).resolve().parent
ENGINE_ROOT = COUNCIL_DIR

from proposal import (
    SEATS_PATH,
    _print_routing_proposal,
    _proposal_lines_for_role,
    _routing_seat_diagnostics,
    _print_static_seat_menu,
    _routing_context_or_exit,
    _routing_enabled,
    load_config,
    resolve_seat,
)
from relay import (
    DEFAULT_MAX_SEATS,
    RelayQuarantine,
    _load_relay_sequence,
    _run_relay_stage,
    write_relay_verdict,
)
from routing import seat_capabilities
from seat_process import (
    DEFAULT_SEAT_TIMEOUT_SECONDS,
    _format_timeout_seconds,
    _resolve_timeout_seconds,
    _timeout_seconds_argument,
)
from session import (
    DEFAULT_TTL_DAYS,
    SESSIONS_DIR,
    _cleanup_sessions,
    _finalize_session,
    _install_shutdown_handlers,
    _set_active_session,
    _write_private_text,
    egress_gate,
    new_session_dir,
)
from verdict import build_brief, run_rounds, write_verdict

DEFAULT_MAX_ROUNDS = 3


def cmd_reset_health(args: argparse.Namespace) -> None:
    from routing import clear_seat_health
    seat_name = getattr(args, "seat", None)
    clear_seat_health(seat_name)
    target = f"seat '{seat_name}'" if seat_name else "all seats"
    print(f"[council] cooldown cleared for {target}.")


def _run_mode(
    args: argparse.Namespace, mode: str, label: str, brief: str,
    role_initial_name: str, role_continue_name: str | None, rounds: int,
    default_routing_role: str,
) -> None:
    config = load_config()
    seat_name, seat = resolve_seat(args, default_routing_role=default_routing_role, config=config)
    egress_gate(brief)
    timeout_seconds = _resolve_timeout_seconds(seat, getattr(args, "timeout_seconds", None))

    keep_session = bool(getattr(args, "keep_session", False))
    session_dir = new_session_dir(label)
    _set_active_session(session_dir, keep_session)
    try:
        _write_private_text(session_dir / "00-brief.md", brief)

        role_initial = (ENGINE_ROOT / "prompts" / role_initial_name).read_text(encoding="utf-8")
        role_continue = (
            (ENGINE_ROOT / "prompts" / role_continue_name).read_text(encoding="utf-8")
            if role_continue_name else None
        )

        if keep_session:
            print(f"[council] session kept: {session_dir}")
        print(
            f"[council] seat: {seat_name} ({seat['model']}) — mode: {mode}, "
            f"timeout {_format_timeout_seconds(timeout_seconds)}s"
        )

        responses, verdicts = run_rounds(
            seat_name, seat, session_dir, mode, brief, role_initial, role_continue, rounds,
            timeout_seconds, config=config,
        )

        write_verdict(session_dir, seat_name, seat, mode, verdicts, responses[-1])

        degraded_suffix = f" (DEGRADED QUORUM: {seat['degraded_reason']})" if seat.get("degraded") else ""
        print(f"[council] final verdict: {verdicts[-1]}{degraded_suffix}")
        if keep_session:
            print(f"[council] file: {session_dir / 'verdict.md'}")
        print()
        print(responses[-1])
    finally:
        _finalize_session(session_dir, keep_session)
        _set_active_session(None)


def cmd_brainstorm(args: argparse.Namespace) -> None:
    rounds = args.rounds
    if rounds < 1:
        sys.exit("[council] --rounds must be at least 1.")
    if args.max_rounds < 1:
        sys.exit("[council] --max-rounds must be at least 1.")
    if rounds > args.max_rounds:
        print(f"[council] --rounds {rounds} exceeds --max-rounds {args.max_rounds}: running only {args.max_rounds} rounds.")
        rounds = args.max_rounds
    brief = build_brief(args.question, args.context)
    _run_mode(
        args, "brainstorm", args.question, brief, "brainstorm.md", "brainstorm-continue.md", rounds, "L-Arch",
    )


def cmd_challenge(args: argparse.Namespace) -> None:
    brief = build_brief(args.plan, args.context)
    _run_mode(args, "challenge", args.plan, brief, "challenge.md", None, 1, "L-Arch")


def cmd_code_review(args: argparse.Namespace) -> None:
    brief = build_brief(None, args.context, diff_path=args.diff)
    _run_mode(args, "code-review", Path(args.diff).name, brief, "code-review.md", None, 1, "L-Code")


def cmd_relay(args: argparse.Namespace) -> None:
    from relay import RelayError

    if getattr(args, "resume", ""):
        _cmd_relay_resume(args)
        return
    if getattr(args, "resumable", False):
        _cmd_relay_start_resumable(args)
        return
    try:
        _cmd_relay_ephemeral(args)
    except RelayError as e:
        sys.exit(str(e))


def _cmd_relay_ephemeral(args: argparse.Namespace) -> None:
    config = load_config()
    seats = config["seats"]
    stages = _load_relay_sequence(args, config, seats)
    brief = build_brief(args.question, args.context, args.diff)
    egress_gate(brief)

    keep_session = bool(getattr(args, "keep_session", False))
    session_dir = new_session_dir(args.question)
    _set_active_session(session_dir, keep_session)
    try:
        _write_private_text(session_dir / "00-brief.md", brief)

        quarantine = RelayQuarantine()
        records = []

        if keep_session:
            print(f"[council] session kept: {session_dir}")
        print(f"[council] mode: relay — stages: {len(stages)}")

        continue_on_reject = bool(getattr(args, "continue_on_reject", False))
        for idx, stage in enumerate(stages, 1):
            record = _run_relay_stage(
                idx, stage, seats, session_dir, brief, records, quarantine,
                getattr(args, "timeout_seconds", None), config,
            )
            records.append(record)
            if record.verdict == "REJECT" and not continue_on_reject and idx < len(stages):
                from verdict import reject_stop_message

                print(reject_stop_message(idx, record.role, len(stages) - idx))
                break

        write_relay_verdict(session_dir, records)
        print(f"[council] final verdict: {records[-1].verdict}")
        if keep_session:
            print(f"[council] file: {session_dir / 'verdict.md'}")
        print()
        print(records[-1].response)
    finally:
        _finalize_session(session_dir, keep_session)
        _set_active_session(None)


def _cmd_relay_start_resumable(args: argparse.Namespace) -> None:
    """Fresh resumable run: same validation and gates as ephemeral, then the graph."""
    from relay import RelayError
    from relay_graph import start_resumable_relay

    try:
        summary = start_resumable_relay(
            question=args.question,
            context=getattr(args, "context", None),
            diff=getattr(args, "diff", None),
            sequence_spec=getattr(args, "sequence", ""),
            max_seats=int(getattr(args, "max_seats", 5)),
            continue_on_reject=bool(getattr(args, "continue_on_reject", False)),
            invocation_timeout=getattr(args, "timeout_seconds", None),
        )
    except RelayError as e:
        sys.exit(str(e))
    print(f"[council] resumable relay {summary['status']}: "
          f"{summary['completed']}/{summary['total']} stages complete.")
    if summary.get("final_verdict"):
        print(f"[council] final verdict: {summary['final_verdict']}")
    if summary.get("final_response"):
        print()
        print(summary["final_response"])


def _cmd_relay_resume(args: argparse.Namespace) -> None:
    """Resume a kept resumable relay session; refuse on identity mismatch.

    The question/context/diff must reproduce the stored brief and the
    sequence must match the stored one: a modified brief or sequence
    refuses without invoking any seat. Exit paths stay here, in the CLI.
    """
    from relay import RelayError
    from relay_graph import resume_relay_session

    try:
        summary = resume_relay_session(
            session_ref=str(getattr(args, "resume") or ""),
            question=args.question,
            context=getattr(args, "context", None),
            diff=getattr(args, "diff", None),
            sequence_spec=getattr(args, "sequence", ""),
            max_seats=int(getattr(args, "max_seats", 5)),
            continue_on_reject=bool(getattr(args, "continue_on_reject", False)),
            invocation_timeout=getattr(args, "timeout_seconds", None),
            allow_uncertain_rerun=bool(getattr(args, "allow_uncertain_rerun", False)),
        )
    except RelayError as e:
        sys.exit(str(e))
    print(f"[council] resumed relay {summary['status']}: "
          f"{summary['completed']}/{summary['total']} stages complete.")
    if summary.get("final_verdict"):
        print(f"[council] final verdict: {summary['final_verdict']}")
    if summary.get("final_response"):
        print()
        print(summary["final_response"])


def cmd_consult(args: argparse.Namespace) -> None:
    from consult import run_consult
    from relay import RelayError

    try:
        config = load_config()
        seats = config["seats"]
        brief = build_brief(args.brief, args.context, args.diff)
        egress_gate(brief)
        keep_session = bool(getattr(args, "keep_session", False))
        session_dir = new_session_dir(args.brief)
        _set_active_session(session_dir, keep_session)
        try:
            _write_private_text(session_dir / "00-brief.md", brief)
            if keep_session:
                print(f"[council] session kept: {session_dir}")
            print(f"[council] mode: consult — seats: {len(args.seat)}")
            result = run_consult(
                seats, brief, list(args.seat), session_dir,
                getattr(args, "timeout_seconds", None), int(args.rebuttals or 0), config,
            )
            print(f"[council] consult complete: {len(result.opinions)} opinions, "
                  f"{len(result.rebuttals)} rebuttals, {len(result.abstentions)} abstentions.")
            tally = ", ".join(f"{verdict}={count}" for verdict, count in sorted(result.tally.items()))
            print(f"[council] tally: {tally or '(none)'}")
            if result.disagreements:
                print("[council] disagreements: " + ", ".join(f"{a} vs {b}" for a, b in result.disagreements))
            # run_consult already flushed each result as it completed.
            if keep_session:
                print(f"[council] file: {session_dir / 'consult.md'}")
        finally:
            _finalize_session(session_dir, keep_session)
            _set_active_session(None)
    except RelayError as e:
        sys.exit(str(e))


def cmd_clean(args: argparse.Namespace) -> None:
    if not SESSIONS_DIR.is_dir():
        print("[council] no sessions to clean.")
        return
    removed = _cleanup_sessions(args.ttl_days, remove_all=args.all, announce=True)
    print(f"[council] cleanup complete: {removed} session(s) removed.")


def cmd_contract(args: argparse.Namespace) -> None:
    """Checks, without invoking any model, that each installed vendor CLI still lists the flags a seat passes it."""
    import cli_contract  # here, not at the top: it needs nexgen_core on sys.path, which `proposal` does first

    problems = 0
    for result in cli_contract.check_all():
        tag = {"ok": "[ OK ]", "absent": "[skip]"}.get(result.status, "[FAIL]")
        print(f"{tag} {result.cli}: {result.detail}")
        problems += result.status in ("drift", "unprobed")
    if problems:
        sys.exit(1)


def cmd_routing_status(args: argparse.Namespace) -> None:
    config = load_config()
    seats = config["seats"]
    if not _routing_enabled(config):
        sys.exit("[council] routing proposal not configured in seats.yaml.")
    plan = _routing_context_or_exit(config)
    capabilities = seat_capabilities(seats)
    print(f"[council] routing document: {plan.source}")
    print(f"[council] host: {socket.gethostname()}")
    for role in plan.roles:
        lines, _ = _proposal_lines_for_role(plan, seats, capabilities, role)
        for line in lines:
            print(line)
    for line in _routing_seat_diagnostics(plan, seats, capabilities):
        print(line)


def cmd_propose(args: argparse.Namespace) -> None:
    """Show the verified menu and leave every execution choice to the human."""
    config = load_config()
    seats = config["seats"]
    if not seats:
        sys.exit(f"[council] {SEATS_PATH} is empty: inert expansion, nothing to do.")
    if not _routing_enabled(config):
        if _print_static_seat_menu(seats):
            print("[council] you choose how many seats to call and rerun with --seat or --sequence.")
        else:
            print("[council] no invocable passive seat is declared on this host.")
        return

    plan = _routing_context_or_exit(config)
    routing = config.get("routing") or {}
    requested_role = getattr(args, "routing_role", None)
    proposal_mode = getattr(args, "proposal_mode", None)

    if requested_role:
        roles = [requested_role]
        title = requested_role
    elif proposal_mode == "relay":
        roles = [str(role) for role in routing.get("relay_roles") or []] or list(plan.roles)
        title = "relay"
    elif proposal_mode:
        role = (routing.get("mode_defaults") or {}).get(proposal_mode)
        if not role:
            sys.exit(
                f"[council] no role proposed for mode '{proposal_mode}': "
                "pass --routing-role ROLE or complete routing.mode_defaults."
            )
        roles = [str(role)]
        title = proposal_mode
    else:
        roles = list(plan.roles)
        title = "all roles"

    has_candidates = _print_routing_proposal(args, config, seats, roles, title=title)
    if not has_candidates:
        print("[council] no candidate is eligible on this host with this policy, nothing to invoke.")
        return
    if proposal_mode == "relay":
        print("[council] you choose how many stages to use and rerun with --sequence role=seat|fallback,...")
    else:
        print("[council] you choose a candidate and rerun the mode with --seat NAME.")


def _add_common_args(parser: argparse.ArgumentParser, *, include_seat: bool = True) -> None:
    if include_seat:
        parser.add_argument("--seat", metavar="NAME", help="seat explicitly chosen by the human")
        parser.add_argument(
            "--routing-role", metavar="ROLE",
            help="document role to propose, e.g. L-Sys, does not start a seat without --seat",
        )
    parser.add_argument(
        "--strict", action="store_true",
        help="fail-closed: do not allow fallback outside Governor approved candidates",
    )
    parser.add_argument(
        "--allow-degraded", action="store_true",
        help="permit fallback outside Governor approved candidates even in non-interactive/CI environments",
    )
    parser.add_argument(
        "--keep-session", action="store_true",
        help="keep local artefacts for debugging, otherwise removed at the end",
    )
    parser.add_argument(
        "--timeout-seconds", metavar="SECONDS", type=_timeout_seconds_argument,
        help=(
            "timeout for this invocation, overrides seat.timeout_seconds "
            f"(default: {int(DEFAULT_SEAT_TIMEOUT_SECONDS)}s)"
        ),
    )


def main() -> int:
    _install_shutdown_handlers()
    ap = argparse.ArgumentParser(prog="council", description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)

    brainstorm = sub.add_parser("brainstorm", help="brainstorming, 1+ round with the proponent replying")
    brainstorm.add_argument("question", help="the question to put to the council")
    brainstorm.add_argument("--context", metavar="FILE", help="context file to attach")
    brainstorm.add_argument("--rounds", type=int, default=1, help="number of rounds (default: 1)")
    brainstorm.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS, help=f"hard cap on rounds (default: {DEFAULT_MAX_ROUNDS})")
    _add_common_args(brainstorm)
    brainstorm.set_defaults(func=cmd_brainstorm)

    challenge = sub.add_parser("challenge", help="an adversarial seat looks for a plan's dominant flaw")
    challenge.add_argument("plan", help="the plan/proposal to put to the test")
    challenge.add_argument("--context", metavar="FILE", help="context file to attach")
    _add_common_args(challenge)
    challenge.set_defaults(func=cmd_challenge)

    code_review = sub.add_parser("code-review", help="cross-vendor review of a diff (vendor different from whoever wrote it)")
    code_review.add_argument("diff", metavar="DIFF_FILE", help="file with the diff/patch to review")
    code_review.add_argument("--context", metavar="FILE", help="additional context file (e.g. why of the change)")
    code_review.add_argument("--author-vendor", metavar="VENDOR", help="vendor who wrote the code: blocks if it matches the seat's vendor")
    _add_common_args(code_review)
    code_review.set_defaults(func=cmd_code_review)

    relay = sub.add_parser("relay", help="sequential multi-seat relay, up to 5 stages")
    relay.add_argument("question", help="brief/question to pass to every stage")
    relay.add_argument("--context", metavar="FILE", help="context file to attach")
    relay.add_argument("--diff", metavar="DIFF_FILE", help="diff/patch to attach to the brief")
    relay.add_argument(
        "--sequence",
        metavar="SPEC|NAME",
        help="inline role=seat|fallback,... or the name of a sequence in seats.yaml",
    )
    relay.add_argument("--max-seats", type=int, default=DEFAULT_MAX_SEATS, help=f"hard cap on stages (1-{DEFAULT_MAX_SEATS})")
    relay.add_argument(
        "--continue-on-reject", action="store_true",
        help="do not stop the relay on an intermediate VERDICT: REJECT (default: stops)",
    )
    relay.add_argument(
        "--resumable", action="store_true",
        help="persist checkpoints in the kept session so the relay can resume after an interruption",
    )
    relay.add_argument(
        "--resume", metavar="SESSION", default="",
        help="resume a kept resumable session (name or path); brief and sequence must match",
    )
    relay.add_argument(
        "--allow-uncertain-rerun", action="store_true",
        help="on resume, re-invoke a stage whose provider response may already exist (quota billed twice at most)",
    )
    _add_common_args(relay, include_seat=False)
    relay.set_defaults(func=cmd_relay)

    consult = sub.add_parser("consult", help="independent parallel opinions plus one aimed rebuttal round")
    consult.add_argument("brief", help="question/plan to put to every seat independently")
    consult.add_argument("--seat", action="append", required=True, help="seat to consult (repeatable, max 5)")
    consult.add_argument("--context", metavar="FILE", help="context file to attach")
    consult.add_argument("--diff", metavar="DIFF_FILE", help="diff/patch to attach to the brief")
    consult.add_argument(
        "--rebuttals", type=int, default=1,
        help="aimed rebuttal rounds on disagreement (0-1; default: 1)",
    )
    _add_common_args(consult, include_seat=False)
    consult.set_defaults(func=cmd_consult)

    clean = sub.add_parser("clean", help="removes sessions past the TTL (retention)")
    clean.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS, help=f"default: {DEFAULT_TTL_DAYS}")
    clean.add_argument("--all", action="store_true", help="removes every session, ignores the TTL")
    clean.set_defaults(func=cmd_clean)

    reset_health = sub.add_parser("reset-health", help="clears active error cooldowns for all seats or a specific seat")
    reset_health.add_argument("--seat", metavar="NAME", help="seat to unblock (default: unblocks all)")
    reset_health.set_defaults(func=cmd_reset_health)

    contract = sub.add_parser("contract", help="checks the vendor CLIs still accept the flags a seat passes them (no model is invoked)")
    contract.set_defaults(func=cmd_contract)

    routing_status = sub.add_parser("routing-status", help="shows the candidates proposed and verified on this host")
    routing_status.set_defaults(func=cmd_routing_status)

    propose = sub.add_parser("propose", help="proposes verified seats, without invoking any model")
    propose.add_argument(
        "--mode", dest="proposal_mode", choices=("brainstorm", "challenge", "code-review", "relay"),
        help="show the proposal for a Council mode",
    )
    propose.add_argument("--routing-role", metavar="ROLE", help="show the proposal for a specific role")
    propose.set_defaults(func=cmd_propose)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
