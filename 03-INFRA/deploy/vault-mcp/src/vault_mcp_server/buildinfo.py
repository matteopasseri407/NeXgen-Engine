"""Which engine commit this image was built from.

One job: read the build identity the Dockerfile baked in, so the health route
can publish it and `nexgen doctor` can compare the deployed half of an
installation with the engine that describes it.

The value arrives as a build argument from `nexgen deploy vault-mcp`. An image
built any other way carries `unknown`, and `engine_commit()` answers None
instead of a placeholder: a route that reported "unknown" as a commit would make
every comparison downstream silently wrong.
"""
from __future__ import annotations

import os

#: The variable the Dockerfile fills from the build argument.
ENGINE_COMMIT_ENV = "NEXGEN_ENGINE_COMMIT"

#: What a build that named no commit carries.
UNKNOWN = "unknown"

#: A short commit: git's own abbreviation, and all the route publishes.
SHORT_COMMIT = 9


def engine_commit() -> str | None:
    """The commit this image was built from, or None when the build did not say."""
    raw = os.environ.get(ENGINE_COMMIT_ENV, "").strip().lower()
    if not raw or raw == UNKNOWN:
        return None
    return raw[:SHORT_COMMIT]
