"""Load seats.yaml / the routing decision, and render the verified, never
model-calling proposal a human picks a seat from.

The private decision document (see routing.py) owns *which model family
fits a role*. This module owns turning that into a host-local menu: which
declared seats can actually run it right now, given what is installed and
what the seat's own CLI reports.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ENGINE_ROOT.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
from config_schema import ConfigValidationError, load_council_config
from routing import (
    RoutingContractError,
    _matches,
    _probe_codex_seat,
    is_pay_per_use,
    load_routing_plan,
    resolve_role_candidates,
    seat_capabilities,
)
from seat_process import _effort_label


def _vault_data_root() -> Path:
    """Same AGENT_ENGINE_ROOT/AGENT_VAULT_DATA pattern as agent_sync.py:
    user data (which seats, which models) lives in the data plane, never in
    the public engine, regardless of where the engine is installed."""
    vault = Path(os.environ.get("KNOWLEDGE_VAULT_PATH") or str(Path.home() / "KnowledgeVault"))
    return Path(os.environ.get("AGENT_VAULT_DATA") or str(vault))


def _seats_path() -> Path:
    """Resolve which seats file this invocation uses.

    Default and unchanged: a single shared seats.yaml in the vault data root
    -- that is 100% of today's installs, and nothing below alters it unless
    one of these two variables is actually set.

    A small team wants more than one person's seat file without everyone
    contending for the same one. Two purely additive, opt-in overrides,
    checked in this order:

      1. COUNCIL_SEATS_FILE: an explicit path to a seats file. Wins outright.
      2. AGENT_TEAM_MEMBER: the same "who am I on this machine" identifier
         documented in 99-INDEX/USER-PROFILE.md -> Team members (optional).
         Resolves to seats.<member>.yaml next to the default file.

    Neither is read unless set, so a mono-user install with a plain
    seats.yaml sees byte-for-byte the same resolution as before this existed.
    """
    council_dir = _vault_data_root() / "03-INFRA" / "agent-universal-layer" / "council"
    override = os.environ.get("COUNCIL_SEATS_FILE")
    if override:
        return Path(override).expanduser()
    member = os.environ.get("AGENT_TEAM_MEMBER")
    if member:
        return council_dir / f"seats.{member}.yaml"
    return council_dir / "seats.yaml"


SEATS_PATH = _seats_path()


def _routing_document_path(config: dict) -> Path:
    routing = config.get("routing") or {}
    decision_file = routing.get("decision_file")
    if not isinstance(decision_file, str) or not decision_file:
        sys.exit("[council] routing proposal not available: decision_file not configured in the data root.")
    return _vault_data_root() / Path(decision_file)


def load_config() -> dict:
    if not SEATS_PATH.is_file():
        sys.exit(
            f"[council] no seats.yaml in the data root ({SEATS_PATH}): inert expansion.\n"
            f"Copy {ENGINE_ROOT / 'seats.yaml.example'} to that path and customize it."
        )
    try:
        return load_council_config(SEATS_PATH)
    except ConfigValidationError as exc:
        sys.exit(f"[council] invalid seats.yaml configuration: {exc}")


def load_seats() -> dict:
    data = load_config()
    seats = data["seats"]
    if not seats:
        sys.exit(f"[council] {SEATS_PATH} is empty: inert expansion, nothing to do.")
    return seats


def _routing_enabled(config: dict) -> bool:
    return bool((config.get("routing") or {}).get("enabled", False))


def _routing_context_or_exit(config: dict):
    try:
        return load_routing_plan(_routing_document_path(config))
    except RoutingContractError as exc:
        sys.exit(f"[council] routing proposal not available: {exc}.")


def _routing_role_for_mode(args: argparse.Namespace, config: dict, default_routing_role: str | None) -> str | None:
    """Map a Council mode to a *proposal* role, never to an execution choice."""
    mode_defaults = ((config.get("routing") or {}).get("mode_defaults") or {})
    return (
        getattr(args, "routing_role", None)
        or mode_defaults.get(getattr(args, "mode", None))
        or default_routing_role
    )


def _proposal_lines_for_role(plan, seats: dict, capabilities: dict, role: str) -> tuple[list[str], bool]:
    """Render locally verified candidates without selecting or invoking one."""
    if role in plan.roles and not plan.roles[role]:
        return [f"  {role}:", "    Explicitly unassigned by the routing document."], False
    try:
        candidates, diagnostics = resolve_role_candidates(plan, seats, capabilities, role)
    except RoutingContractError as exc:
        return [f"  {role}: not defined in the document, {exc}."], False

    lines = [f"  {role}:"]
    if candidates:
        for index, name in enumerate(candidates, 1):
            seat = seats[name]
            effort_label = _effort_label(seat)
            retention = (
                "zero-retention verified"
                if seat.get("zero_retention", False)
                else "WARNING: no verified zero-retention"
            )
            cost = _seat_cost(plan, name, seat)
            pay_note = f", WARNING: pay-per-use ({cost})" if is_pay_per_use(cost) else ""
            lines.append(
                f"    {index}. {name}: {seat['model']} via {seat['cli']}{effort_label}, {retention}{pay_note}."
            )
    else:
        lines.append("    No compatible local seat.")
    if diagnostics:
        lines.append("    Excluded: " + "; ".join(diagnostics[:4]) + ".")
    return lines, bool(candidates)


def _print_routing_proposal(
    args: argparse.Namespace, config: dict, seats: dict, roles: list[str], *, title: str,
) -> bool:
    """Show a host-local, policy-aware menu. This function never calls a model."""
    plan = _routing_context_or_exit(config)
    capabilities = seat_capabilities(seats)
    has_candidates = False
    print(f"[council] proposal for {title}. No model was called.")
    for role in roles:
        lines, role_has_candidates = _proposal_lines_for_role(plan, seats, capabilities, str(role))
        has_candidates = has_candidates or role_has_candidates
        for line in lines:
            print(line)
    return has_candidates


def _print_static_seat_menu(seats: dict) -> bool:
    print("[council] no private routing configured. Declared seats, you choose:")
    has_invocable_seat = False
    for name, seat in seats.items():
        has_invocable_seat = True
        effort_label = _effort_label(seat)
        retention = (
            ""
            if seat.get("zero_retention", False)
            else ", WARNING: no verified zero-retention"
        )
        print(f"  {name}: {seat['model']} via {seat['cli']}{effort_label}{retention}.")
    return has_invocable_seat


def _require_human_single_selection(
    args: argparse.Namespace, config: dict, seats: dict, default_routing_role: str | None,
) -> None:
    role = _routing_role_for_mode(args, config, default_routing_role)
    if _routing_enabled(config):
        if role:
            has_candidates = _print_routing_proposal(
                args, config, seats, [role], title=f"{getattr(args, 'mode', 'Council')} / {role}",
            )
        else:
            has_candidates = _print_routing_proposal(args, config, seats, [], title=getattr(args, "mode", "Council"))
    else:
        has_candidates = _print_static_seat_menu(seats)
    if has_candidates:
        sys.exit(
            "[council] human choice required: rerun with --seat NAME. "
            "--routing-role only narrows the proposal, it does not start a seat."
        )
    sys.exit("[council] no eligible seat to select: fix the mapping, CLI, or policy shown above.")


def _seat_quota_pool(seat: dict) -> str:
    if seat.get("quota_pool"):
        return str(seat["quota_pool"])
    model_prefix = str(seat["model"]).split("/", 1)[0]
    if seat.get("cli") == "opencode":
        return model_prefix
    return f"{seat.get('cli', 'unknown')}:{model_prefix}"


def _warn_if_explicit_codex_seat_not_default(seat_name: str, seat: dict) -> None:
    """An explicit --seat bypasses the routing probe by design (the human
    decided). But for a codex seat that bypass can silently hide a stale
    default: if the seat's model/effort no longer match Codex's own
    config.toml, every call is forwarded with an explicit -m instead of
    riding the CLI default the human may still believe is active. Only
    codex is probed here (a local config.toml read); other CLIs would need
    a subprocess probe, too costly for this non-blocking, informational
    path."""
    if seat.get("cli") != "codex":
        return
    capability = _probe_codex_seat(seat)
    if capability.available:
        return
    print(
        f"[council] warning: seat '{seat_name}' is not the current default of the codex CLI "
        f"({capability.reason}); it will be forwarded explicitly with -m."
    )


def _seat_cost(plan, seat_name: str, seat: dict) -> str | None:
    """The raw Costo cell of the first candidate matching this seat, if any.

    Used for the pay-per-use warning and confirmation gate. A seat without a
    matching candidate in the routing document has no stated cost, so no
    gate applies: there is nothing to confirm.
    """
    if not hasattr(plan, "roles"):
        return None
    for candidates in plan.roles.values():
        for candidate in candidates:
            if _matches(seat, candidate):
                return candidate.cost
    return None


def _warn_no_zero_retention(seat_name: str, seat: dict) -> None:
    if seat.get("zero_retention", False):
        return
    print(
        f"[council] WARNING: seat '{seat_name}' has no verified zero-retention guarantee; "
        "data sent may be retained or used for model training.",
        file=sys.stderr,
    )


def _confirm_pay_per_use(seat_name: str, seat: dict, cost: str | None) -> None:
    """Real-money gate: a pay-per-use seat is never invoked without the human
    confirming it, because the call spends actual money on a per-use channel.
    This is a hard stop, not a warning: input() reads the operator's terminal
    and a non-yes answer aborts before any process is spawned."""
    if not is_pay_per_use(cost):
        return
    print(
        f"[council] WARNING: seat '{seat_name}' ({seat['model']}) runs on a "
        f"pay-per-use channel (stated cost: '{cost}'). This call spends real money.",
        file=sys.stderr,
    )
    answer = input(f"Confirm pay-per-use call for seat '{seat_name}'? [y/N] ").strip().casefold()
    if answer not in ("y", "yes"):
        sys.exit(f"[council] STOP: pay-per-use seat '{seat_name}' not confirmed.")


def _check_seat_allowed(
    seat_name: str,
    seat: dict,
    args: argparse.Namespace,
    config: dict | None = None,
) -> None:
    del args
    _warn_no_zero_retention(seat_name, seat)
    if config is None:
        if not SEATS_PATH.is_file():
            return
        config = load_config()
    if _routing_enabled(config):
        plan = _routing_context_or_exit(config)
        _confirm_pay_per_use(seat_name, seat, _seat_cost(plan, seat_name, seat))


def resolve_seat(args: argparse.Namespace, *, default_routing_role: str | None = None) -> tuple[str, dict]:
    config = load_config()
    seats = config["seats"]
    if not seats:
        sys.exit(f"[council] {SEATS_PATH} is empty: inert expansion, nothing to do.")
    seat_name = getattr(args, "seat", None)
    if not seat_name:
        _require_human_single_selection(args, config, seats, default_routing_role)
    if seat_name not in seats:
        sys.exit(f"[council] unknown seat: {seat_name}. Available: {', '.join(seats)}")
    seat = seats[seat_name]
    _check_seat_allowed(seat_name, seat, args, config=config)
    _warn_if_explicit_codex_seat_not_default(seat_name, seat)
    author_vendor = getattr(args, "author_vendor", None)
    if author_vendor and seat["vendor"].lower() == author_vendor.lower():
        sys.exit(
            f"[council] STOP: seat '{seat_name}' is from the same vendor ({seat['vendor']}) "
            "as the material under review. Cross-vendor review requires a vendor different from "
            "whoever produced the material (--author-vendor)."
        )
    return seat_name, seat
