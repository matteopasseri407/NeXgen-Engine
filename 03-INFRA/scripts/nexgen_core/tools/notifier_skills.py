"""update-notifier third-party notice. Reads state/report, never touches boot. Owned here, re-exported by update_notifier for compat."""

from __future__ import annotations

import time
from pathlib import Path

from nexgen_core.paths import resolve_state_dir


from .notifier_state import CACHE_TOO_OLD_TO_NAG_DAYS, _read_state, _record_skills_dismissal, _skills_dismissed, _write_state


def _applied_file() -> Path:
    return Path(resolve_state_dir()) / "nexgen" / "third-party-applied.json"


def _short_skill_name(what: str) -> str:
    """Backward-compat wrapper: single implementation in nexgen_core.thirdparty_names."""
    from nexgen_core.thirdparty_names import short_name

    return short_name(what)


def _take_fresh_applied(mark: bool = True) -> list[str]:
    """Names bumped since this lane last spoke. Marks them shown.

    At-least-once by design: if two shells race, an update may be
    announced twice, but never zero times. A lost announcement would
    silently hide work the machine did on its own. Callers that only
    peek (the GUI before its dialog) pass mark=False and confirm later.
    """
    try:
        import json

        data = json.loads(_applied_file().read_text(encoding="utf-8"))
        entries = data.get("applied") if isinstance(data, dict) else None
        entries = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    except (OSError, ValueError):
        return []
    state = _read_state()
    shown = state.get("skills_shown")
    shown = set(shown) if isinstance(shown, list) else set()

    def _marker(entry: dict) -> str:
        return f"{entry.get('what')}|{entry.get('new')}"

    fresh = [e for e in entries if _marker(e) not in shown]
    if mark:
        shown.update(_marker(e) for e in fresh)
        _write_state({"skills_shown": sorted(shown)[-200:]})
    seen: set[str] = set()
    names = []
    for entry in fresh:
        name = _short_skill_name(str(entry.get("what") or ""))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _confirm_skills_shown() -> None:
    """Marks currently pending announcements as shown, after they were
    actually displayed. A crashed dialog must not consume the message."""
    _skills_notice(mark=True)


def _held_once_daily(mark: bool = True) -> str | None:
    """One quiet line for held items, at most once a day per held set."""
    try:
        import hashlib
        import json

        guard_file = Path(resolve_state_dir()) / "nexgen" / "third-party-guard.json"
        data = json.loads(guard_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if (time.time() - float(data.get("checked_at", 0))) > CACHE_TOO_OLD_TO_NAG_DAYS * 86400:
            return None
        held = data.get("hold") if isinstance(data.get("hold"), list) else []
        held = [h for h in held if isinstance(h, dict) and h.get("what")]
        if not held:
            return None
        fingerprint = hashlib.sha256(
            "\n".join(sorted(str(h["what"]) for h in held)).encode()
        ).hexdigest()[:16]
        if _skills_dismissed("dismissed_skills_hold", fingerprint):
            return None
        if mark:
            _record_skills_dismissal("dismissed_skills_hold", fingerprint)
        return (
            f"{len(held)} aggiornamenti delicati in attesa "
            f"({', '.join(_short_skill_name(str(h['what'])) for h in held[:3])}"
            f"{', ...' if len(held) > 3 else ''}): dimmi e li leggo io."
        )
    except (OSError, ValueError, TypeError):
        return None


def _skills_notice(mark: bool = True) -> str | None:
    """The whole third-party message for every lane: what moved on its
    own, what is ready for one yes, plus one line for what is held.
    No questions, ever."""
    parts = []
    applied = _take_fresh_applied(mark=mark)
    if applied:
        parts.append(f"Skill aggiornate: {', '.join(applied)}.")
    batch_line = _batch_once_daily(mark=mark)
    if batch_line:
        parts.append(batch_line)
    held_line = _held_once_daily(mark=mark)
    if held_line:
        parts.append(held_line)
    return "\n".join(parts) or None


def _batch_once_daily(mark: bool = True) -> str | None:
    """Names the BATCH verdicts once a day, so the human knows a single
    `nexgen skill bump` is waiting. Read-only: the approval stays theirs."""
    try:
        import hashlib
        import json

        guard_file = Path(resolve_state_dir()) / "nexgen" / "third-party-guard.json"
        data = json.loads(guard_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if (time.time() - float(data.get("checked_at", 0))) > CACHE_TOO_OLD_TO_NAG_DAYS * 86400:
            return None
        batch = data.get("batch") if isinstance(data.get("batch"), list) else []
        batch = [b for b in batch if isinstance(b, dict) and b.get("what")]
        if not batch:
            return None
        fingerprint = hashlib.sha256(
            ("\n".join(sorted(str(b["what"]) for b in batch)) + "|batch").encode()
        ).hexdigest()[:16]
        if _skills_dismissed("dismissed_skills_batch", fingerprint):
            return None
        if mark:
            _record_skills_dismissal("dismissed_skills_batch", fingerprint)
        names = ", ".join(_short_skill_name(str(b["what"])) for b in batch[:4])
        if len(batch) > 4:
            names += ", ..."
        return f"{len(batch)} aggiornamenti tranquilli pronti ({names}): `nexgen skill bump` li alza in un colpo solo."
    except (OSError, ValueError, TypeError):
        return None
