# nexgen: one-job reason="manage supervisor routing tiers, candidate selection, quota exhaustion and model discovery"
"""Routing tiers for the supervisor: tier-based model selection, inventory and trial.

One job: read `routing-tiers.yaml` from the vault data root, manage the local
exhaustion and promotion state, select candidates by volume and inventory, and
run deterministic coding trials for newly discovered Flash models.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from nexgen_core.files import atomic_write_text
from nexgen_core.paths import resolve_home, resolve_vault_data

from .config import lane_state_dir
from .supervision_state import SupervisionError

ROUTING_TIERS_FILE = "routing-tiers.yaml"
ROUTING_STATE_FILE = "routing-tiers-state.json"
DEFAULT_VOLUME_MIN_5H = 20000
DEFAULT_EXHAUSTION_HOURS = 6.0

QUOTA_PATTERNS = (
    re.compile(r"monthly\s+usage\s+limit\s+reached|usage\s+limit\s+reached|quota\s+exceeded", re.I),
    re.compile(r"insufficient_quota|resource_exhausted|resource\s+has\s+been\s+exhausted", re.I),
    re.compile(r"credit\s+balance\s+is\s+too\s+low|plan\s+is\s+spent|out\s+of\s+credits", re.I),
    re.compile(r"429.*(?:limit|quota|usage|exceeded|rate)|(?:limit|quota|usage|exceeded|rate).*429", re.I),
)


class RoutingTiersError(SupervisionError):
    """Errors arising from tier routing, candidate selection or trials."""


def routing_tiers_path(vault: Path | None = None) -> Path:
    """Where routing-tiers.yaml lives: honors env var override or vault root."""
    override = os.environ.get("ROUTING_TIERS_PATH")
    if override:
        return Path(override)
    root = resolve_vault_data(override=vault)
    return root / "03-INFRA" / "agent-universal-layer" / ROUTING_TIERS_FILE


def routing_tiers_exists(vault: Path | None = None) -> bool:
    """Whether routing-tiers.yaml exists on this machine."""
    return routing_tiers_path(vault).is_file()


def load_tiers(path: Path | None = None, vault: Path | None = None) -> dict[str, Any]:
    """Parse routing-tiers.yaml into a dictionary; empty dict if missing."""
    target = path or routing_tiers_path(vault)
    if not target.is_file():
        return {}
    try:
        raw = target.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        raise RoutingTiersError(f"impossibile leggere {target}: {exc}") from exc


def lane_state_file(home: Path | None = None) -> Path:
    """Path to the persistent local JSON state for routing tiers."""
    override = os.environ.get("ROUTING_STATE_PATH")
    if override:
        return Path(override)
    base = lane_state_dir() if home is None else resolve_home(home) / ".local/state/nexgen/local-lane"
    return base / ROUTING_STATE_FILE


def load_state(state_file: Path | None = None) -> dict[str, Any]:
    """Load the lane's routing state, initializing missing structures."""
    target = state_file or lane_state_file()
    if not target.is_file():
        return {"exhausted": {}, "promoted_order": [], "previous_order": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"exhausted": {}, "promoted_order": [], "previous_order": []}
        data.setdefault("exhausted", {})
        data.setdefault("promoted_order", [])
        data.setdefault("previous_order", [])
        return data
    except (OSError, ValueError):  # nexgen: allow reason="unreadable state resets to empty"
        return {"exhausted": {}, "promoted_order": [], "previous_order": []}


def save_state(state: dict[str, Any], state_file: Path | None = None) -> None:
    """Atomically save the lane's routing state."""
    target = state_file or lane_state_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(state, indent=2))


def clean_expired_exhausted(state: dict[str, Any], now: float | None = None) -> bool:
    """Remove expired exhausted model entries; returns True if state changed."""
    moment = time.time() if now is None else now
    exhausted = state.get("exhausted", {})
    expired = [m for m, rec in exhausted.items() if moment >= float(rec.get("expires_at", 0))]
    for model in expired:
        exhausted.pop(model, None)
    return bool(expired)


def is_exhausted(model: str, state: dict[str, Any] | None = None, now: float | None = None,
                 state_file: Path | None = None) -> bool:
    """Check if model is exhausted according to local state."""
    st = state if state is not None else load_state(state_file)
    clean_expired_exhausted(st, now=now)
    record = st.get("exhausted", {}).get(model)
    if not record:
        return False
    moment = time.time() if now is None else now
    return moment < float(record.get("expires_at", 0))


def is_quota_exhausted_error(error_or_text: Any) -> tuple[bool, str]:
    """Detect if an error message/exception corresponds to provider quota exhaustion."""
    text = str(error_or_text or "")
    for pattern in QUOTA_PATTERNS:
        match = pattern.search(text)
        if match:
            return True, match.group(0)
    return False, ""


def mark_exhausted(model: str, reason: str, *, ttl_seconds: float = DEFAULT_EXHAUSTION_HOURS * 3600,
                   state_file: Path | None = None) -> None:
    """Record model as exhausted for 6 hours."""
    st = load_state(state_file)
    now = time.time()
    st.setdefault("exhausted", {})[model] = {
        "exhausted_at": now,
        "expires_at": now + ttl_seconds,
        "reason": str(reason),
    }
    save_state(st, state_file)


def _candidate_model(c: Any) -> str:
    """Extract model name string from candidate entry."""
    return str(c.get("model") if isinstance(c, dict) else c).strip()


def _candidate_harness(c: Any) -> str:
    """Extract harness name from candidate entry or default."""
    if isinstance(c, dict) and c.get("harness"):
        return str(c["harness"]).strip()
    model = _candidate_model(c)
    if model.startswith("opencode-go/") or model.startswith("opencode/"):
        return "opencode"
    if "gemini" in model.lower():
        return "antigravity"
    return "opencode"


def allowed_for(tier: str | None = None, *, allow_limited: bool = True,
                path: Path | None = None) -> tuple[str, ...]:
    """The exact list of allowed models for a tier, without globs."""
    data = load_tiers(path)
    tiers = data.get("tiers", {})
    if not tiers:
        return ()
    st = load_state()
    promoted = st.get("promoted_order", [])
    if tier == "worker":
        models = [_candidate_model(c) for c in tiers.get("worker", [])]
        for p in promoted:
            if p not in models:
                models.append(p)
        if allow_limited:
            models.extend(_candidate_model(c) for c in tiers.get("limited", []))
        return tuple(m for m in models if m)
    if tier == "limited":
        return tuple(m for m in (_candidate_model(c) for c in tiers.get("limited", [])) if m)
    if tier == "orchestrator":
        return tuple(m for m in (_candidate_model(c) for c in tiers.get("orchestrator", [])) if m)
    all_models: list[str] = list(promoted)
    for t_name in ("worker", "limited", "orchestrator"):
        for c in tiers.get(t_name, []):
            name = _candidate_model(c)
            if name and name not in all_models:
                all_models.append(name)
    return tuple(all_models)


def model_family(model: str) -> str:
    """Extract model family by stripping effort suffix (-low, -medium, -high)."""
    tag = (model or "").strip()
    for suffix in ("-low", "-medium", "-high"):
        if tag.endswith(suffix):
            return tag[:-len(suffix)]
    return tag


def check_model_allowed(model: str, tier: str = "worker", *, allow_limited: bool = False,
                        path: Path | None = None) -> None:
    """Refuse a model outside the allowed tier models with a human-readable list."""
    allowed = allowed_for(tier, allow_limited=allow_limited, path=path)
    if not allowed:
        return
    tag = (model or "").strip()
    family = model_family(tag)
    if tag not in allowed and family not in allowed:
        allowed_str = ", ".join(allowed)
        raise RoutingTiersError(
            f"modello «{model}» non consentito per il tier {tier}. Modelli ammessi: {allowed_str}"
        )


def resolve_worker_candidate(model: str = "", harness: str = "", *, limited: bool = False,
                             path: Path | None = None) -> tuple[str, str]:
    """Resolve and validate the worker candidate for a unit."""
    if not routing_tiers_exists(path):
        return model, harness
    if model:
        check_model_allowed(model, tier="worker", allow_limited=limited, path=path)
        if not harness:
            data = load_tiers(path)
            all_c = data.get("tiers", {}).get("worker", []) + data.get("tiers", {}).get("limited", [])
            for c in all_c:
                cand = _candidate_model(c)
                if cand == model or cand == model_family(model):
                    harness = _candidate_harness(c)
                    break
        return model, harness
    return _first_checked_worker(harness, limited=limited, path=path)


def _first_checked_worker(harness: str, *, limited: bool, path: Path | None) -> tuple[str, str]:
    """The first worker candidate the quota preflight does not find spent; one try per candidate."""
    from . import routing_discovery
    attempts = max(1, len(allowed_for("worker", allow_limited=limited, path=path)))
    for _ in range(attempts):
        hit = pick("worker", allow_limited=limited, path=path)
        exhausted, _reason = routing_discovery.preflight(hit["model"], hit["harness"])
        if not exhausted:
            return hit["model"], harness or hit["harness"]
    raise RoutingTiersError("nessun candidato worker disponibile: quota esaurita su tutti i candidati")


def handle_quota_failover(model: str, error_or_reason: Any, *, limited: bool = False,
                          path: Path | None = None, megaphone: Any = None) -> dict[str, Any] | None:
    """Handle quota exhaustion: mark model exhausted, notify via megaphone, pick next."""
    is_quota, reason = is_quota_exhausted_error(error_or_reason)
    if not is_quota:
        return None
    mark_exhausted(model, reason)
    from nexgen_core.megaphone import Megaphone
    phone = megaphone or Megaphone()
    phone.send_alert(
        title="Modello esaurito per fine quota",
        message=f"Il modello «{model}» ha esaurito la quota ({reason}). Passaggio al candidato successivo.",
        action=None,
        alert_key=f"quota_exhausted:{model}",
    )
    try:
        next_cand = pick("worker", allow_limited=limited, path=path)
        if next_cand["model"] == model:
            return None
        return next_cand
    except RoutingTiersError:  # nexgen: allow reason="no next candidate available returns None"
        return None


def probe_cli_inventory(harness: str) -> set[str]:
    """Scan local CLI inventory for a given harness."""
    canonical = "antigravity" if harness in ("agy", "antigravity") else harness
    if canonical == "opencode" and shutil.which("opencode"):
        try:
            res = subprocess.run([shutil.which("opencode"), "models"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", check=False, timeout=10,
                                 stdin=subprocess.DEVNULL)
            if res.returncode == 0:
                return {line.strip() for line in res.stdout.splitlines() if line.strip()}
        except Exception:  # nexgen: allow reason="unreachable opencode models returns empty"
            return set()
    if canonical == "antigravity" and shutil.which("agy"):
        try:
            res = subprocess.run([shutil.which("agy"), "models"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", check=False, timeout=10,
                                 stdin=subprocess.DEVNULL)
            if res.returncode == 0:
                models = set()
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if parts:
                        models.add(parts[0])
                return models
        except Exception:  # nexgen: allow reason="unreachable agy models returns empty"
            return set()
    return set()


def is_in_inventory(model: str, harness: str, *, inventory: set[str] | None = None,
                    probe_fn: Callable[[str], set[str]] | None = None) -> bool:
    """Whether model is detected in machine inventory for harness."""
    inv = inventory if inventory is not None else (probe_fn or probe_cli_inventory)(harness)
    if not inv:
        return True
    m_lower = model.casefold()
    for item in inv:
        i_lower = item.casefold()
        if m_lower == i_lower or i_lower.startswith(m_lower) or m_lower.startswith(i_lower):
            return True
    return False


def _order_candidates(candidates: list[Any], promoted: list[str],
                      data: dict[str, Any] | None = None) -> list[Any]:
    """Sort candidates placing promoted order first."""
    if not promoted:
        return list(candidates)
    by_model = {_candidate_model(c): c for c in candidates}
    ordered: list[Any] = []
    channels = (data or {}).get("channels_volume", {})
    for m in promoted:
        if m in by_model:
            ordered.append(by_model.pop(m))
        elif m in channels:
            ordered.append({"model": m, "harness": "opencode", "requests_5h": channels[m]})
    for rem in candidates:
        if _candidate_model(rem) in by_model:
            ordered.append(rem)
            by_model.pop(_candidate_model(rem), None)
    return ordered


def _match_candidate(c: Any, data: dict, min_vol: int, st: dict,
                     probe_fn: Any) -> dict[str, Any] | None:
    """Evaluate candidate volume, exhaustion, and inventory."""
    model = _candidate_model(c)
    harness = _candidate_harness(c)
    vol = c.get("requests_5h") if isinstance(c, dict) and c.get("requests_5h") is not None \
        else data.get("channels_volume", {}).get(model, 0)
    if vol < min_vol:
        return None
    if is_exhausted(model, state=st):
        return None
    if not is_in_inventory(model, harness, probe_fn=probe_fn):
        return None
    return {"model": model, "harness": harness, "requests_5h": vol}


def pick(tier: str = "worker", *, allow_limited: bool = False, path: Path | None = None,
         state_file: Path | None = None, inventory_probe: Any = None) -> dict[str, Any]:
    """Select the first non-exhausted candidate with sufficient volume and present in inventory."""
    data = load_tiers(path)
    if not data:
        raise RoutingTiersError("nessuna configurazione routing-tiers.yaml trovata")
    st = load_state(state_file)
    if clean_expired_exhausted(st):
        save_state(st, state_file)
    tiers = data.get("tiers", {})
    if allow_limited and tier == "worker":
        for c in tiers.get("limited", []):
            m, h = _candidate_model(c), _candidate_harness(c)
            if not is_exhausted(m, state=st) and is_in_inventory(m, h, probe_fn=inventory_probe):
                return {"model": m, "harness": h, "tier": "limited", "requests_5h": 0}
    candidates = tiers.get(tier, [])
    if tier == "worker":
        min_vol = int(data.get("volume_min_5h", DEFAULT_VOLUME_MIN_5H))
        candidates = _order_candidates(candidates, st.get("promoted_order", []), data=data)
        for c in candidates:
            hit = _match_candidate(c, data, min_vol, st, inventory_probe)
            if hit:
                hit["tier"] = tier
                return hit
    else:
        for c in candidates:
            m, h = _candidate_model(c), _candidate_harness(c)
            if not is_exhausted(m, state=st) and is_in_inventory(m, h, probe_fn=inventory_probe):
                return {"model": m, "harness": h, "tier": tier, "requests_5h": 0}
    raise RoutingTiersError(f"nessun candidato disponibile per il tier {tier}")


def promote(model: str, *, path: Path | None = None, state_file: Path | None = None) -> list[str]:
    """Promote model to top of worker tier order, storing previous order for rollback."""
    st = load_state(state_file)
    data = load_tiers(path)
    workers = [_candidate_model(c) for c in data.get("tiers", {}).get("worker", [])]
    current = st.get("promoted_order") or workers
    st["previous_order"] = list(current)
    new_order = [model] + [m for m in current if m != model]
    st["promoted_order"] = new_order
    save_state(st, state_file)
    return new_order


def rollback(*, state_file: Path | None = None) -> bool:
    """Restore the previous candidate order; returns True if restored."""
    st = load_state(state_file)
    prev = st.get("previous_order")
    if not prev:
        return False
    st["promoted_order"] = list(prev)
    st["previous_order"] = []
    save_state(st, state_file)
    return True
