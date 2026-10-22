"""The contract between the supervision engine and a harness it can drive.

One job: say what a harness must be able to do for each step the engine performs,
and make every operation a harness does not have a declared absence instead of a
guess. The engine asks the adapter; an adapter that cannot do something raises
`Unsupported`, and the verb says so in words rather than pretending the step
happened. Nothing here starts a process: the adapters next door build command
lines, read stores and write profiles, and the engine keeps the launch policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..supervision_state import SupervisionError


class AdapterError(SupervisionError):
    """The harness is missing, its store is unreadable, or a step cannot be prepared."""


class Unsupported(AdapterError):
    """This harness does not have this operation, and the adapter says so instead of faking it."""

    def __init__(self, harness: str, operation: str, why: str = "") -> None:
        detail = f" ({why})" if why else ""
        super().__init__(f"l'harness {harness} non sa {operation}{detail}")
        self.harness = harness
        self.operation = operation


#: The operations the engine can ask of an adapter, in the order the verbs use them.
OPERATIONS = (
    "open_tab", "find_session", "send_turn", "live_state", "cost", "tokens",
    "write_permissions", "resume", "scout", "dispose_location", "session_model",
    "last_turn",
)


@dataclass(frozen=True)
class Capabilities:
    """What one harness can really do, declared once and printed where it matters.

    A `False` is not a bug to work around: it is the truth about that harness, and
    the engine answers "non lo so fare" instead of inventing a session id, a cost
    or a permission profile. `deny_push` says how a push is stopped, because an
    adapter that cannot deny it must not claim it did.
    """

    open_tab: bool = True
    find_session: bool = False
    send_turn: bool = False
    live_state: bool = False
    cost: bool = False
    tokens: bool = False
    write_permissions: bool = False
    resume: bool = False
    scout: bool = False
    dispose_location: bool = False
    session_model: bool = False
    last_turn: bool = False
    deny_push: str = ""

    def has(self, operation: str) -> bool:
        """Whether this harness declares the operation."""
        return bool(getattr(self, operation, False))

    def missing(self) -> tuple[str, ...]:
        """Every operation this harness does not have, named for the owner."""
        return tuple(name for name in OPERATIONS if not self.has(name))

    def line(self) -> str:
        """One line for a report: what is there, and what is declared absent."""
        missing = self.missing()
        absent = ", ".join(missing) if missing else "niente"
        push = self.deny_push or "nessuna regola"
        return f"sa: {', '.join(name for name in OPERATIONS if self.has(name))} · manca: {absent} · push: {push}"


@dataclass(frozen=True)
class SessionRef:
    """A session and the folder it was born in, the two facts the engine reuses it by."""

    id: str
    folder: str = ""
    title: str = ""
    created_ms: int = 0

    def as_dict(self) -> dict:
        """The shape the older callers and the journal already read."""
        return {
            "id": self.id, "directory": self.folder, "title": self.title,
            "created": self.created_ms,
        }


class Adapter:
    """The methods the engine calls; every one a harness lacks raises `Unsupported`."""

    name = "?"
    label = "?"
    executable = "?"
    capabilities = Capabilities()

    def require(self, operation: str, why: str = "") -> None:
        """Refuse before anything is opened when this harness does not have the operation."""
        if not self.capabilities.has(operation):
            raise Unsupported(self.name, operation, why)

    def tab_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "") -> list[str]:
        """The command that opens a visible tab on this folder, with the prompt as a pointer."""
        raise Unsupported(self.name, "open_tab")

    def send_argv(self, executable: str, session: str, prompt: str, *, model: str = "") -> list[str]:
        """The command that puts one turn into an existing session without opening a tab."""
        raise Unsupported(self.name, "send_turn")

    def resume_argv(self, executable: str, session: str, folder: Path, prompt: str = "") -> list[str]:
        """The command that opens a visible tab on the session the task already has."""
        raise Unsupported(self.name, "resume")

    def find_session(self, folder: Path, since_ms: int, **kwargs):
        """The session opened in this folder at or after `since_ms`, or None."""
        raise Unsupported(self.name, "find_session")

    def write_profile(self, task_id: str, folder: Path, *, model: str = "") -> None:
        """Put the executor's permission profile in the copy before its tab opens."""
        raise Unsupported(self.name, "write_permissions")

    def measure(self, session: str):
        """What the session cost and how it is doing, children included."""
        raise Unsupported(self.name, "cost")

    def session_model(self, session: str) -> str:
        """The model tag this session runs on, when the harness reports one."""
        raise Unsupported(self.name, "session_model")

    def last_turn(self, session: str):
        """How the session's last turn closed, for the failed-delivery check."""
        raise Unsupported(self.name, "last_turn")

    def scout_argv(self, executable: str, prompt: str) -> list[str]:
        """The command that runs one reconnaissance round, read-only where the harness can."""
        raise Unsupported(self.name, "scout")

    def dispose_location(self, folder: Path) -> tuple[bool, str]:
        """Free the harness's servers bound to this folder before the copy is removed."""
        raise Unsupported(self.name, "dispose_location")


def normal_path(value: str | Path) -> str:
    """A folder as one comparable string, case and separators aside.

    Windows reports the same worktree as `C:\\x`, `c:/x` and `\\\\?\\C:\\x`; the
    engine compares them for session affinity, so all three must collapse.
    """
    text = str(value or "").replace("\\", "/")
    if text.startswith("//?/"):
        text = text[4:]
    return text.rstrip("/").casefold()
