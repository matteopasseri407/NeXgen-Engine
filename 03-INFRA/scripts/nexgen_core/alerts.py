"""Every alert the megaphone can send, in the words a person reads.

One job: own the text of each alert an engine raises on somebody's screen -- the
title, the sentence, the single action -- so the wording can be read, changed and
measured in one place instead of being buried in the rule that decided to speak.

Why a catalog rather than strings at the call sites. An alert is read by someone
who is not debugging anything: a person who installed this to keep their notes in
order. Two things go wrong quietly when the words live where the logic does. The
first is jargon: "agent sync is not running" is written for whoever wrote the
check, and the person cannot tell what to do with it. The second is length: a
toast shows a title on one line and the body in about four, so a body that grows
past the limit loses its tail, and the tail is where the action was. A test walks
every alert below in both languages and refuses both failures, which it can only
do because they are all here.

The English sentence is the source and the lookup key; `locales/it.py` carries the
Italian. An alert with nothing to do carries an empty action, and one whose text
depends on a number names that number in braces and gives a sample, so the test
can measure the sentence it will really send.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexgen_core.i18n import t

#: A Windows toast shows the title on one line and the body in about four. Past
#: these, Windows cuts the text itself and the sentence holding the action is what
#: disappears.
TITLE_LIMIT = 60
BODY_LIMIT = 200


@dataclass(frozen=True)
class Alert:
    """One alert: what happened, in one sentence, and the one thing to do about it.

    `sample` fills the fields for whoever needs to see the finished sentence
    without a real failure to hand -- the length test above all, which would
    otherwise measure a template full of braces instead of what a person reads.
    """

    title: str
    message: str
    action: str = ""
    sample: dict[str, Any] = field(default_factory=dict)

    def say(self, **fields: Any) -> tuple[str, str, str]:
        """The three parts as the reader gets them: title, message, action."""
        return (
            t(self.title, **fields),
            t(self.message, **fields),
            t(self.action, **fields) if self.action else "",
        )


#: The engine stopped keeping this machine aligned.
SYNC_STALLED = Alert(
    title="NeXgen: the alignment stopped",
    message="{hours} hours without a check, so your notes may be out of date.",
    action="nexgen sync",
    sample={"hours": "4.2"},
)

#: The engine cannot read its own record of the last check, so it is watching nothing.
SYNC_MONITORING_BLIND = Alert(
    title="NeXgen: the engine cannot check itself",
    message="Its own record of the last check is unreadable, so nothing is being watched.",
    action="nexgen doctor",
)

#: An unattended update found a release it cannot trust, and installed nothing.
UPDATE_REFUSED = Alert(
    title="NeXgen: the update was blocked",
    message="The new version is not signed by a key this computer trusts, so nothing was installed.",
    action="nexgen update --check",
)

#: An unattended update failed midway and the previous version was restored.
UPDATE_ROLLED_BACK = Alert(
    title="NeXgen: the update was undone",
    message="The version that worked is back. This release will not be installed on its own.",
    action="nexgen update",
)

#: An unattended update failed midway and could not be undone.
UPDATE_ROLLBACK_FAILED = Alert(
    title="NeXgen: the update stopped halfway",
    message="The engine may be only partly updated. Read the report before you try again.",
    action="nexgen doctor",
)

#: A run that keeps this machine aligned did not start, and will not start by itself.
UNIT_NOT_STARTED = Alert(
    title="NeXgen: the recurring check did not start",
    message="Until it runs, this computer stops keeping itself up to date.",
    action="nexgen doctor",
)

#: The engine's own diagnostic found something a person has to look at.
MACHINE_PROBLEMS = Alert(
    title="NeXgen: something needs your attention",
    message="The engine's own check found problems. The report says what each one is.",
    action="nexgen doctor",
)

#: One plan's allowance crossed a threshold that a person has to act on.
QUOTA_AT = Alert(
    title="Quota at {percent}%: {provider}",
    message="{provider} has used {percent}% of the {window} allowance. It resets in {age}.",
    action="nexgen quota",
    sample={"percent": "86", "provider": "Codex (free)", "window": "5h", "age": "2h 10m"},
)

#: The same alert when the provider did not say when the allowance comes back.
QUOTA_AT_NO_RESET = Alert(
    title="Quota at {percent}%: {provider}",
    message="{provider} has used {percent}% of the {window} allowance.",
    action="nexgen quota",
    sample={"percent": "86", "provider": "Codex (free)", "window": "5h"},
)

#: Chats that a recurring tidy-up should have closed are still open, and growing.
CHATS_PILING_UP = Alert(
    title="NeXgen: the chats are piling up",
    message="The last tidy-up closed none of them. Chats still waiting: {count}.",
    action="nexgen openclaw groom",
    sample={"count": "14"},
)

#: A model nobody has priced appeared in the inventory.
MODEL_UNKNOWN_VOLUME = Alert(
    title="NeXgen: a new model has no known cost",
    message="The model {model} turned up, but nobody wrote down how much work it gets.",
    action="channels_volume in routing-tiers.yaml",
    sample={"model": "deepseek-v4.1-flash"},
)

#: A model nobody had tried answered well enough to be adopted.
MODEL_PASSED = Alert(
    title="NeXgen: a new model passed its test",
    message="The model {model} answered correctly and is ready to be adopted.",
    action="nexgen models promote {model}",
    sample={"model": "deepseek-v4.1-flash"},
)

#: A model nobody had tried answered badly.
MODEL_FAILED = Alert(
    title="NeXgen: a new model failed its test",
    message="The model {model} did not answer correctly: {reason}.",
    sample={"model": "deepseek-v4.1-flash", "reason": "no usable answer"},
)

#: A task that hit its limit was moved to another model, and the person is told.
WORKER_RELAUNCHED = Alert(
    title="NeXgen: a blocked task was restarted",
    message="One task hit its limit and is running again on another model.",
)

#: A task hit its limit and nowhere else to go, so it stays stopped.
WORKER_STUCK = Alert(
    title="NeXgen: a task is stuck",
    message="One task hit its limit and no other model was free, so it stays stopped.",
)

#: A model used up its allowance in the middle of a job.
MODEL_OUT_OF_QUOTA = Alert(
    title="NeXgen: a model ran out of quota",
    message="The model {model} has no allowance left ({reason}). The next one takes over.",
    sample={"model": "deepseek-v4.1-flash", "reason": "rate limit reached"},
)

#: The notification a person asks for by hand, to see one arrive.
TEST_NOTIFICATION = Alert(
    title="NeXgen: the notification works",
    message="This is how an alert from the engine looks on this screen.",
    action="nexgen doctor",
)


def catalog() -> dict[str, Alert]:
    """Every alert, by the name a test and a reader can talk about."""
    return {
        name: value
        for name, value in sorted(globals().items())
        if isinstance(value, Alert)
    }
