"""Council's one line at the end, through the engine's one alert surface.

One job: when the caller asked to be told (`--notify`), turn what a run produced
into one message and hand it to the megaphone. Nothing here decides whether the
run succeeded: the caller passes the summary the run recorded, the session it
kept, or the failure that stopped it.

Why the megaphone and not `openclaw message send` from somewhere else: the
engine has exactly one surface allowed to speak to the person (contract
invariant 7). A second one would be the same alert arriving twice, or a channel
the person configured that nobody else knows about. Council calls a run that
nobody is watching -- typically from a chat, minutes after the turn that started
it closed -- so the channel is not available to it and the megaphone is.
"""
from __future__ import annotations

from nexgen_core.i18n import t

#: The key the megaphone debounces on. One run, one key: two councils asked in the
#: same hour must both arrive, while a retried announcement of the same run must not.
def alert_key(tag: str, mode: str) -> str:
    return f"council:{tag or mode or 'run'}"


def outcome_lines(
    mode: str, tag: str, summary: str, session_dir: str, failure: str,
) -> tuple[str, str, str]:
    """The three parts of the message: what happened, what it concluded, where the rest is."""
    if failure:
        title = t("Council {mode} stopped", mode=mode)
    else:
        title = t("Council {mode} finished", mode=mode)
    parts: list[str] = []
    if tag:
        parts.append(t("Request: {tag}.", tag=tag))
    if failure:
        parts.append(t("It stopped before the end: {reason}", reason=failure))
    elif summary:
        parts.append(t("Outcome: {summary}.", summary=summary))
    else:
        parts.append(t("It ended without a verdict to quote."))
    action = t("Full outcome: {path}", path=session_dir) if session_dir else ""
    return title, " ".join(parts), action


def announce_outcome(
    mode: str,
    tag: str = "",
    summary: str = "",
    session_dir: str = "",
    failure: str = "",
    megaphone=None,
) -> str:
    """Send the one line and return what was said, for the run's own log.

    A megaphone that cannot reach a channel returns a sentence too: the caller is
    a process with no console, so the log is the only place the attempt can exist.
    """
    if megaphone is None:
        from nexgen_core.megaphone import Megaphone

        megaphone = Megaphone()
    title, message, action = outcome_lines(mode, tag, summary, session_dir, failure)
    delivered = megaphone.send_alert(title, message, action, alert_key=alert_key(tag, mode))
    if not delivered:
        return f"[council] not delivered: {title} — {message}"
    return f"[council] notified: {title} — {message}"


def run_notified(args, outcome: dict) -> int:
    """Run the mode the arguments name, then say how it ended, whatever the ending was.

    A chat or a schedule starts this process and is not there when it finishes, so the
    ending is the one thing that must not be left in a console nobody reads. `outcome` is
    the record the mode handlers fill in (`mode`, `session`, `summary`); it is cleared
    first, so a previous run in the same process cannot answer for this one.

    The failure is re-raised untouched: the notification reports it, it never replaces it.
    """
    outcome.update(mode="", session="", summary="")
    failure = ""
    try:
        args.func(args)
    except SystemExit as exc:
        failure = str(exc.code) if isinstance(exc.code, str) else f"exit {exc.code}"
        raise
    except Exception as exc:  # noqa: BLE001 - the message is the point, and the error is re-raised untouched
        failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        kept = bool(getattr(args, "keep_session", False))
        print(announce_outcome(
            outcome["mode"] or str(getattr(args, "mode", "") or "council"),
            tag=str(getattr(args, "tag", "") or ""),
            summary=outcome["summary"],
            session_dir=outcome["session"] if kept else "",
            failure=failure,
        ))
    return 0
