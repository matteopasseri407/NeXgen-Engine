"""One root for every error the engine raises on purpose.

Each module used to declare its own exception with a different builtin
parent (`Exception`, `ValueError`, `RuntimeError`, `TimeoutError`), so
"catch everything the engine meant" was inexpressible: a top-level
handler either caught `Exception` (swallowing `KeyboardInterrupt`-adjacent
noise and real bugs with it) or enumerated six names that drifted apart.
`NexgenError` is the shared root; every existing parent is kept alongside
it through multiple inheritance, so no `except` clause in the tree changes
meaning -- `except TimeoutError` still catches the lock timeout,
`except ValueError` still catches a bad manifest, and `except NexgenError`
now catches exactly the engine's own failures and nothing else.
"""
from __future__ import annotations


class NexgenError(Exception):
    """Root of every deliberate engine failure. Never raised directly:
    raise the specific subclass that says what went wrong."""
