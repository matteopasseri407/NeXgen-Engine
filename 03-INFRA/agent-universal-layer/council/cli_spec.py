"""The `council` command line: every mode, every option and every default, in one place.

One job: turn a list of arguments into the namespace the mode handlers read. It holds no
behaviour of its own and imports no handler -- they arrive as a mapping, so this file and the
orchestrator cannot import each other -- and it is the only place that decides which flag
belongs to which mode. Each mode is one small function, so a flag is added where it belongs
and a reader can see one mode's whole surface at once.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping

from relay import DEFAULT_MAX_SEATS
from seat_process import DEFAULT_SEAT_TIMEOUT_SECONDS, _timeout_seconds_argument
from session import DEFAULT_TTL_DAYS

#: How many rounds a brainstorm may ask for before the hard cap refuses it.
DEFAULT_MAX_ROUNDS = 3

Handler = Callable[[argparse.Namespace], None]


def _common_args(parser: argparse.ArgumentParser, *, include_seat: bool = True) -> None:
    """The flags every mode shares, in one definition so they cannot drift apart."""
    if include_seat:
        parser.add_argument("--seat", metavar="NAME", help="seat explicitly chosen by the human")
        parser.add_argument("--auto", action="store_true", help="choose a seat without asking (default: ask)")
        parser.add_argument("--routing-role", metavar="ROLE",
                            help="document role to propose, e.g. L-Sys; does not start a seat by itself")
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
        "--notify", action="store_true",
        help=(
            "tell the person how this run ended, through the engine's alert surface: "
            "for a run nobody is watching, started from a chat or a schedule"
        ),
    )
    parser.add_argument(
        "--tag", metavar="ID", default="",
        help="identifier of this run, quoted back in the notification",
    )
    parser.add_argument(
        "--timeout-seconds", metavar="SECONDS", type=_timeout_seconds_argument,
        help=(
            "timeout for this invocation, overrides seat.timeout_seconds "
            f"(default: {int(DEFAULT_SEAT_TIMEOUT_SECONDS)}s)"
        ),
    )


def _brainstorm(sub, handlers: Mapping[str, Handler]) -> None:
    """One seat, one or more rounds; each round after the first must attack the previous one."""
    parser = sub.add_parser("brainstorm", help="brainstorming, 1+ round with the proponent replying")
    parser.add_argument("question", help="the question to put to the council")
    parser.add_argument("--context", metavar="FILE", help="context file to attach")
    parser.add_argument("--rounds", type=int, default=1, help="number of rounds (default: 1)")
    parser.add_argument(
        "--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
        help=f"hard cap on rounds (default: {DEFAULT_MAX_ROUNDS})",
    )
    _common_args(parser)
    parser.set_defaults(func=handlers["brainstorm"])


def _challenge(sub, handlers: Mapping[str, Handler]) -> None:
    """One adversarial seat, looking for the dominant flaw in a plan."""
    parser = sub.add_parser("challenge", help="an adversarial seat looks for a plan's dominant flaw")
    parser.add_argument("plan", help="the plan/proposal to put to the test")
    parser.add_argument("--context", metavar="FILE", help="context file to attach")
    _common_args(parser)
    parser.set_defaults(func=handlers["challenge"])


def _code_review(sub, handlers: Mapping[str, Handler]) -> None:
    """Cross-vendor review of a diff: the seat's vendor must differ from the author's."""
    parser = sub.add_parser(
        "code-review", help="cross-vendor review of a diff (vendor different from whoever wrote it)",
    )
    parser.add_argument("diff", metavar="DIFF_FILE", help="file with the diff/patch to review")
    parser.add_argument("--context", metavar="FILE", help="additional context file (e.g. why of the change)")
    parser.add_argument(
        "--author-vendor", metavar="VENDOR",
        help="vendor who wrote the code: blocks if it matches the seat's vendor",
    )
    _common_args(parser)
    parser.set_defaults(func=handlers["code-review"])


def _relay(sub, handlers: Mapping[str, Handler]) -> None:
    """A sequential staffage: each stage sees the brief and the previous stage's output."""
    parser = sub.add_parser("relay", help="sequential multi-seat relay, up to 5 stages")
    parser.add_argument("question", help="brief/question to pass to every stage")
    parser.add_argument("--context", metavar="FILE", help="context file to attach")
    parser.add_argument("--diff", metavar="DIFF_FILE", help="diff/patch to attach to the brief")
    parser.add_argument(
        "--sequence",
        metavar="SPEC|NAME",
        help="inline role=seat|fallback,... or the name of a sequence in seats.yaml",
    )
    parser.add_argument(
        "--max-seats", type=int, default=DEFAULT_MAX_SEATS,
        help=f"hard cap on stages (1-{DEFAULT_MAX_SEATS})",
    )
    parser.add_argument(
        "--continue-on-reject", action="store_true",
        help="do not stop the relay on an intermediate VERDICT: REJECT (default: stops)",
    )
    parser.add_argument(
        "--resumable", action="store_true",
        help="persist checkpoints in the kept session so the relay can resume after an interruption",
    )
    parser.add_argument(
        "--resume", metavar="SESSION", default="",
        help="resume a kept resumable session (name or path); brief and sequence must match",
    )
    parser.add_argument(
        "--allow-uncertain-rerun", action="store_true",
        help="on resume, re-invoke a stage whose provider response may already exist (quota billed twice at most)",
    )
    _common_args(parser, include_seat=False)
    parser.set_defaults(func=handlers["relay"])


def _consult(sub, handlers: Mapping[str, Handler]) -> None:
    """Independent opinions in parallel, then at most one aimed rebuttal round."""
    parser = sub.add_parser("consult", help="independent parallel opinions plus one aimed rebuttal round")
    parser.add_argument("brief", help="question/plan to put to every seat independently")
    parser.add_argument("--seat", action="append", required=True, help="seat to consult (repeatable, max 5)")
    parser.add_argument("--context", metavar="FILE", help="context file to attach")
    parser.add_argument("--diff", metavar="DIFF_FILE", help="diff/patch to attach to the brief")
    parser.add_argument(
        "--rebuttals", type=int, default=1,
        help="aimed rebuttal rounds on disagreement (0-1; default: 1)",
    )
    _common_args(parser, include_seat=False)
    parser.set_defaults(func=handlers["consult"])


def _upkeep(sub, handlers: Mapping[str, Handler]) -> None:
    """The verbs that invoke no model: retention, cooldowns, the CLI contract, the proposal."""
    clean = sub.add_parser("clean", help="removes sessions past the TTL (retention)")
    clean.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS, help=f"default: {DEFAULT_TTL_DAYS}")
    clean.add_argument("--all", action="store_true", help="removes every session, ignores the TTL")
    clean.set_defaults(func=handlers["clean"])

    reset_health = sub.add_parser(
        "reset-health", help="clears active error cooldowns for all seats or a specific seat",
    )
    reset_health.add_argument("--seat", metavar="NAME", help="seat to unblock (default: unblocks all)")
    reset_health.set_defaults(func=handlers["reset-health"])

    contract = sub.add_parser(
        "contract", help="checks the vendor CLIs still accept the flags a seat passes them (no model is invoked)",
    )
    contract.set_defaults(func=handlers["contract"])

    routing_status = sub.add_parser(
        "routing-status", help="shows the candidates proposed and verified on this host",
    )
    routing_status.set_defaults(func=handlers["routing-status"])

    propose = sub.add_parser("propose", help="proposes verified seats, without invoking any model")
    propose.add_argument(
        "--mode", dest="proposal_mode", choices=("brainstorm", "challenge", "code-review", "relay"),
        help="show the proposal for a Council mode",
    )
    propose.add_argument("--routing-role", metavar="ROLE", help="show the proposal for a specific role")
    propose.set_defaults(func=handlers["propose"])


def build_parser(
    handlers: Mapping[str, Handler],
    *,
    description: str | None = None,
) -> argparse.ArgumentParser:
    """The command line, with each mode wired to the handler the caller passed in."""
    ap = argparse.ArgumentParser(prog="council", description=description)
    sub = ap.add_subparsers(dest="mode", required=True)
    _brainstorm(sub, handlers)
    _challenge(sub, handlers)
    _code_review(sub, handlers)
    _relay(sub, handlers)
    _consult(sub, handlers)
    _upkeep(sub, handlers)
    return ap
