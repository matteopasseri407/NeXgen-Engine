"""Shared state and budgets for the bounded loop and persistent research.

State contains JSON-serializable data only. Policy, actions and drivers use
these same types; changing their fields also changes the checkpoint contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Hard cap on loop steps. Muse: "loop senza N" is an anti-pattern.
MAX_STEPS = 6
#: The menu never grows beyond this: a short closed list, not a catalog.
MAX_MENU = 5
#: Continuations of a truncated read before the loop must answer or escalate:
#: 4 windows of read_chars keep even a long document bounded for a 12B.
MAX_CONTINUATIONS = 3
#: One observation line is truncated to this; the prompt keeps only the last
#: MAX_PROMPT_OBSERVATIONS of them plus a counter for the earlier steps.
MAX_OBSERVATION_CHARS = 400
MAX_PROMPT_OBSERVATIONS = 2
#: Two consecutive searches with no results narrow the menu.
MAX_EMPTY_STREAK = 2


@dataclass
class Candidate:
    """One admissible action, with its concrete argument when it has one."""

    action: str
    arg: str = ""


@dataclass
class Decision:
    """One step decision, kept for transparency and debugging."""

    step: int
    action: str
    arg: str
    why: str = ""
    ok: bool = True
    detail: str = ""
    #: Wall time for the whole step (decision + execution), for the p95 metric.
    elapsed_s: float = 0.0
    #: Wall time of the decision call alone: the model's own latency.
    decide_s: float = 0.0


@dataclass
class StepResult:
    task: str
    answer: str = ""
    receipts: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    steps: int = 0
    escalated: bool = False
    injection: bool = False
    confabulation: bool = False
    problems: list[str] = field(default_factory=list)
    collected: str = ""
    #: Proposal ids produced in the loop, returned by the engine itself:
    #: the caller must not depend on the model echoing them in prose.
    mail_draft: str = ""
    upload_proposal: str = ""
    #: One guided rewrite when the claim check fails; bounded, never a loop.
    correction_used: bool = False
    corrections: int = 0


@dataclass
class LoopState:
    task: str
    route: str = "none"
    named_path: str = ""
    step: int = 0
    receipts: list[dict[str, Any]] = field(default_factory=list)
    tried_queries: list[str] = field(default_factory=list)
    tried_paths: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    #: Only the retrieved *outputs* (not the tool-call echo): an arg reused
    #: from a previous query is not "content steering".
    content_seen: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    #: Successful web outputs, kept apart from file reads so the final answer
    #: can cite freshly searched results instead of losing them.
    web: list[str] = field(default_factory=list)
    #: Mail and Drive reads, same treatment: engine-found content for the answer.
    mail: list[str] = field(default_factory=list)
    drive: list[str] = field(default_factory=list)
    #: Calendar reads, same treatment.
    calendar: list[str] = field(default_factory=list)
    #: Outlook reads, same treatment (read-only in the loop; replies go
    #: through the gmail/outlook compose gate, not model choices).
    outlook: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)
    #: Engine-found ids (never model-invented) offered as read candidates.
    mail_ids: list[str] = field(default_factory=list)
    drive_ids: list[str] = field(default_factory=list)
    calendar_ids: list[str] = field(default_factory=list)
    outlook_ids: list[str] = field(default_factory=list)
    #: Ids already read: the menu keeps offering the rest, so comparing two
    #: results never requires finding them again.
    read_ids: list[str] = field(default_factory=list)
    #: Task-level intents, engine-detected: when the request asks to reply to
    #: a mail just read, or to upload a file just read, the menu offers the
    #: gated propose action — never a direct send.
    want_reply: bool = False
    want_upload: bool = False
    #: Engine-known references for the propose actions: last mail id read,
    #: last file path read, and the resulting proposal ids for the answer.
    last_mail_id: str = ""
    mail_draft: str = ""
    upload_proposal: str = ""
    #: Last source read that can be continued: engine-known tool, args and
    #: coverage straight from the registry. Empty when the last read was
    #: complete (or a fake that records no coverage).
    last_read: dict[str, Any] = field(default_factory=dict)
    #: Windows already consumed on the current read: bounded, never a drain.
    continuations: int = 0
    #: Rendered proposal blocks for the final answer prompt: the answer is
    #: built from collected content, so a proposal the prompt never sees is
    #: a proposal the answer can never cite.
    mail_draft_preview: str = ""
    upload_preview: str = ""
    #: Engine-worded approval pointers appended to the final answer verbatim:
    #: the user receives id, recipient and approval command even when the
    #: model's prose stays terse. Tagged [motore: ...], never model prose.
    proposal_footers: list[str] = field(default_factory=list)
    empty_streak: int = 0
    decisions: list[Decision] = field(default_factory=list)


#: The request asks the 12B to reply (not just read) or to upload to Drive:
#: the loop may then offer the gated propose actions. Detection is
#: engine-side, on the task text — never on retrieved content.
REPLY_INTENT_RE = re.compile(r"\b(rispondi|rispondere|invia|inviare|manda una mail|scrivi una mail)\b", re.I)
UPLOAD_INTENT_RE = re.compile(r"\b(carica|caricare|upload|pubblica su drive)\b", re.I)
