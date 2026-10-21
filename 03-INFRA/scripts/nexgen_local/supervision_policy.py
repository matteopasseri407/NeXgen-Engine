"""The lane's supervision settings: the model list and the stall threshold.

One job: read the owner's `supervision` section and answer the two questions the
rest of the engine asks of it. An empty or absent model list means no
restriction, and the engine imposes nothing on somebody who wrote nothing. A
list that exists is matched against the tag the harness reports,
`provider/model#variant`, with the shell's own glob rule, so `provider/*` covers
every model of that provider and every variant of each. The values live in the
lane's config file, never in this module.
"""
from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path

from . import supervision_costs as costs
from . import supervision_state as state
from .supervision_state import SupervisionError

#: Where the lane keeps its own settings, beside the model tiers map.
CONFIG_FILE = "config.json"
#: The section and the key the list lives under, as the owner writes them.
SECTION = "supervision"
KEY = "allowed_models"
#: How long an active session may stand still before it is called stalled.
STALL_KEY = "stall_minutes"
DEFAULT_STALL_MINUTES = 20.0


class PolicyError(SupervisionError):
    """The lane's settings exist but do not hold the policy they claim to hold."""


def config_path() -> Path:
    """The lane's settings file, in the lane's own state directory."""
    return state.lane_state_dir() / CONFIG_FILE


def allowed_models(path: str | Path | None = None) -> tuple[str, ...]:
    """The configured globs, or an empty tuple when the owner wrote no list.

    A file that exists but does not parse, or whose section is not an object of
    strings, is an error: reading it as "no restriction" would silently allow
    exactly what the list was written to stop.
    """
    target, section = _section(path)
    listed = section.get(KEY, [])
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise PolicyError(f"`{SECTION}.{KEY}` deve essere un elenco di schemi: {target}")
    return tuple(item.strip() for item in listed if item.strip())


def stall_minutes(path: str | Path | None = None) -> float:
    """How many minutes a session may stand still before `sup status` calls it stalled."""
    _target, section = _section(path)
    value = section.get(STALL_KEY, DEFAULT_STALL_MINUTES)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise PolicyError(
            f"`{SECTION}.{STALL_KEY}` deve essere un numero di minuti maggiore di zero: {value!r}"
        )
    return float(value)


def _section(path: str | Path | None = None) -> tuple[Path, dict]:
    """The `supervision` section as written, or an empty one when there is no file."""
    target = Path(path).expanduser() if path else config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:  # nexgen: allow reason="nessun file significa nessuna impostazione, che è il default dichiarato"
        return target, {}
    except OSError as exc:
        raise PolicyError(f"configurazione della lane non leggibile: {target} ({exc})") from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise PolicyError(f"configurazione della lane non è JSON: {target}") from exc
    if not isinstance(parsed, dict):
        raise PolicyError(f"configurazione della lane non è un oggetto: {target}")
    section = parsed.get(SECTION, {})
    if not isinstance(section, dict):
        raise PolicyError(f"`{SECTION}` nella configurazione della lane non è un oggetto: {target}")
    return target, section


def model_allowed(model: str, allowed: tuple[str, ...] | list[str] | None = None) -> bool:
    """Whether this tag is on the list; no list allows everything.

    A session whose model nobody could read is not on a list that exists: the
    engine cannot say it is allowed, and assuming it is would be the silence the
    list exists to remove. A tag matches the full form and the part before the
    variant, so `provider/*` covers `provider/model#max`.
    """
    patterns = tuple(allowed or ())
    if not patterns:
        return True
    tag = (model or "").strip()
    if not tag:
        return False
    bare = tag.split("#", 1)[0]
    return any(fnmatchcase(candidate, pattern) for pattern in patterns for candidate in (tag, bare))


def refusal(model: str, allowed: tuple[str, ...] | list[str] | None = None) -> str:
    """The one line naming the model and the list, for a journal row and a printed reason."""
    return (
        f"modello {model or '(non leggibile)'} fuori dall'elenco consentito: "
        f"{', '.join(allowed or ())}"
    )


def check_session(record: dict, allowed: tuple[str, ...], measure=None) -> tuple[bool, str]:
    """Whether the unit's session is on the list, and the one line that says why.

    No list is a pass with its reason. With a list, a session nobody attached
    and a model nobody could read both fail: "not verified" is not "allowed",
    and this is the only check that says which model the unit ran on.
    """
    if not allowed:
        return True, "nessun elenco di modelli consentiti"
    session = str(record.get("session") or "")
    if not session:
        return False, "nessuna sessione agganciata: il modello non è verificabile"
    try:
        facts = (measure or costs.measure)(session)
    except costs.CostError as exc:
        return False, f"modello non leggibile ({exc})"
    model = str(getattr(facts, "model", "") or "")
    if not model_allowed(model, allowed):
        return False, refusal(model, allowed)
    return True, f"modello {model} nell'elenco consentito"
