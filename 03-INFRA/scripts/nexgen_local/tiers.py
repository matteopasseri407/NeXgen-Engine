"""Capability tiers for the local lane ("the tutor").

The lane was built for 4B-12B models: the engine routes, ranks and prescribes
every step while the model fills slots. That steering is what makes a tiny
model usable, and it is also what holds a larger one back (fixed
route->retrieve->answer, small router context, thinking off, one forced action
per step). A larger model measured 0/4 on the injection trap suite without the
cage, so tiers loosen steering by tier and NEVER safety: every tier keeps the
read-only surface, the fail-closed audit, the receipt-checked answer and the
sanitised retrieval path. Tier assignment is by evidence (the trap and
functional suites), never by declared size.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Tier:
    """One capability tier: knobs the existing code already reads, nothing new.

    router_full_ctx: the router call uses the configured full context instead
        of the small 4B-sized window.
    thinking: the router and decision channels may think, the way prose
        already does where the model supports it. Off stays off for small
        models: a thinking trace before a forced-schema choice measured 12/16
        with the decision p95 going from 5s to 170s.
    max_steps: the budget of the bounded loop.
    engine_picks_tool: True keeps the closed engine menu (the engine prescribes
        the next step); False lets the model choose freely among the read-only
        retrieval tools and decide when it is done.
    """

    name: str
    router_full_ctx: bool
    thinking: bool
    max_steps: int
    engine_picks_tool: bool


#: Today's behaviour, unchanged: the default for any unknown model.
TUTOR = Tier(name="tutor", router_full_ctx=False, thinking=False, max_steps=6, engine_picks_tool=True)

#: The engine still routes and ranks; the model gets its configured full
#: context for the router too, thinking is allowed, more steps in the loop.
GUIDED = Tier(name="guided", router_full_ctx=True, thinking=True, max_steps=10, engine_picks_tool=True)

#: The model chooses freely among the read-only retrieval tools in the bounded
#: loop and decides when it is done; thinking on; full context.
ASSISTED = Tier(name="assisted", router_full_ctx=True, thinking=True, max_steps=12, engine_picks_tool=False)

TIERS: dict[str, Tier] = {"tutor": TUTOR, "guided": GUIDED, "assisted": ASSISTED}

#: Promotion order: each step loosens steering, never safety.
TIER_ORDER = ("tutor", "guided", "assisted")

#: Fallback for any unknown model or tier name: the strictest tier.
DEFAULT_TIER = "tutor"

#: File holding the per-model tier map, under the lane's own state directory.
MODEL_TIERS_FILE = "model_tiers.json"

#: Registry tools the assisted tier may trigger: the read-only retrieval
#: surface. Gated propose verbs (draft_mail, propose_upload) and loop verbs
#: (answer, escalate, continue_read) are not registry tools; they stage
#: proposals for human approval and never apply anything themselves.
ASSISTED_TOOL_NAMES = frozenset(
    {
        "search_vault",
        "read_vault",
        "read_repo",
        "read_pdf",
        "web_search",
        "search_mail",
        "read_mail",
        "search_drive",
        "read_drive",
        "search_calendar",
        "read_calendar",
        "search_outlook",
        "read_outlook",
    }
)


def _get_tier_impl(name: str | None) -> Tier:
    key = str(name or "").strip().casefold()
    if key not in TIERS:
        raise ValueError(f"tier sconosciuto: {name!r} (attesi: {', '.join(TIER_ORDER)})")
    tier = TIERS[key]
    assert_tier_invariants(tier)
    return tier


def get_tier(name: str | None) -> Tier:
    """The tier by name; unknown names fail loudly instead of guessing."""
    return _get_tier_impl(name)


def _tier_for_model_impl(model: str | None, mapping: dict[str, Any] | None = None) -> str:
    if not model or not str(model).strip():
        return DEFAULT_TIER
    name = str((mapping or {}).get(str(model).strip(), DEFAULT_TIER))
    return name if name in TIERS else DEFAULT_TIER


def tier_for_model(model: str | None, mapping: dict[str, Any] | None = None) -> str:
    """The stored tier for a model; any unknown model stays on tutor.

    Never raises: at the config boundary an unknown name must degrade to the
    strictest tier, not abort the lane.
    """
    return _tier_for_model_impl(model, mapping)


def _next_tier_impl(name: str) -> str:
    key = str(name or "").strip().casefold()
    if key not in TIERS:
        return DEFAULT_TIER
    order = list(TIER_ORDER)
    return order[min(order.index(key) + 1, len(order) - 1)]


def next_tier(name: str) -> str:
    """The tier above `name`; the top tier promotes to itself."""
    return _next_tier_impl(name)


def _effective_max_steps_impl(cfg: Any, explicit: int | None = None) -> int:
    if explicit is None:
        return get_tier(getattr(cfg, "tier", DEFAULT_TIER) or DEFAULT_TIER).max_steps
    return int(explicit)


def effective_max_steps(cfg: Any, explicit: int | None = None) -> int:
    """The loop budget: an explicit flag wins, otherwise the model's tier.

    Callers that always passed a default (the CLI's --max-steps) now pass
    None when the flag is absent, so tutor resolves to today's default while
    higher tiers get their wider budget from the same knob.
    """
    return _effective_max_steps_impl(cfg, explicit)


class TierInvariantError(RuntimeError):
    """A tier that would relax something no tier may relax."""


def _assert_tier_invariants_impl(tier: Tier) -> None:
    from .tools import READ_ONLY_TOOL_NAMES

    # Explicit raises, not asserts: `python -O` strips asserts, and these
    # are the guarantees that make loosening a tier safe at all.
    problems = []
    if not isinstance(tier.engine_picks_tool, bool):
        problems.append("engine_picks_tool must be a bool")
    if not isinstance(tier.thinking, bool):
        problems.append("thinking must be a bool")
    if not isinstance(tier.router_full_ctx, bool):
        problems.append("router_full_ctx must be a bool")
    if not (isinstance(tier.max_steps, int) and tier.max_steps >= 1):
        problems.append("max_steps must stay bounded and positive")
    if not tier.engine_picks_tool:
        outside = set(ASSISTED_TOOL_NAMES) - set(READ_ONLY_TOOL_NAMES)
        if outside:
            problems.append(f"free tool choice must stay read-only, found {sorted(outside)}")
    if problems:
        raise TierInvariantError(f"tier {tier.name}: " + "; ".join(problems))


def assert_tier_invariants(tier: Tier) -> None:
    """Invariants no tier relaxes; run on every tier resolution.

    A tier only tunes steering (context, thinking, budget, who picks the
    tool). The read-only surface, the fail-closed audit, the receipt check and
    the sanitised retrieval path live in the shared code below and are
    identical for every tier.
    """
    _assert_tier_invariants_impl(tier)


def _tiers_file_impl(state_dir: Path | None = None) -> Path:
    if state_dir is not None:
        return Path(state_dir) / MODEL_TIERS_FILE
    from .config import lane_state_dir

    return lane_state_dir() / MODEL_TIERS_FILE


def tiers_file(state_dir: Path | None = None) -> Path:
    """Where the per-model tier map lives; overrideable for tests."""
    return _tiers_file_impl(state_dir)
