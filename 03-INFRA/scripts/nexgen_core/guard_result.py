"""What a guard cycle is asked to do, and what it reports back.

One job: hold the two value types of the guard contract -- the requested
`GuardMode` and the returned `GuardResult` -- so the coordinator and every
phase component can name them without importing the coordinator itself.
They used to live in `guard.py`; a component that only reports an outcome
should not have to import the whole cycle (and start a cycle-shaped import
graph) to say so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class GuardMode(str, Enum):
    """The four things a sync transaction can be asked for."""

    GUARD = "guard"
    APPLY = "apply"
    PULL = "pull"
    PREFLIGHT = "preflight"


@dataclass
class GuardResult:
    """The outcome of one transaction: verdict, mode, message, exit code, actions."""

    success: bool
    mode: GuardMode
    message: str
    exit_code: int = 0
    actions_taken: list[str] = field(default_factory=list)
