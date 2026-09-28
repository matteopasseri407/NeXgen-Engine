"""Optional governed lane for local models in NeXgen Engine.

The lane is read-only by construction, decides its tools on the engine side,
writes a receipt for every call, and treats retrieved content as data: a
hidden instruction in a note, a PDF or a web result never becomes an order.
See ``docs/local-lane.md`` and run ``nexgen-local doctor``.
"""

__all__ = ["config", "engine", "tools"]
