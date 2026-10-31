"""One job: let this account install npm's global packages, moving them to `~/.local` when it must.

Why it exists: the usual server has a Node the administrator installed in
`/usr/local`, and the gateway's account cannot write there. The setup used to stop
with "run 'npm config set prefix ~/.local' once", which is npm's own answer and
touches nothing but this account's `~/.npmrc`. On a clean Ubuntu 24.04 that stop
was the end of a client install, so the setup now gives that answer itself.

It only moves the prefix npm chose on its own (the folder its Node lives in). A
prefix someone set is theirs: then the setup still stops and says why.
"""
from __future__ import annotations

import os
from collections.abc import Callable, MutableMapping
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw import host_preflight
from nexgen_core.openclaw_service import RunResult

Run = Callable[..., RunResult]


def ensure_user_prefix(run: Run, npm: str, env: MutableMapping[str, str], out: Callable[[str], None]) -> bool:
    """True when npm can install global packages for this account, after moving them if needed."""
    prefix = host_preflight.npm_prefix(run, npm, env)
    if prefix is None or host_preflight.is_writable(prefix):
        return True
    moved = _move_to_home(run, npm, prefix, env)
    if moved is None:
        out(t("npm installs global packages under {prefix}, which this user cannot write.", prefix=prefix))
        out(t("Run 'npm config set prefix ~/.local' once, or use a version manager for Node, "
              "then run the setup again."))
        return False
    out(t("npm installs global packages under {prefix}, which this user cannot write: this account's go to "
          "{moved} from now on (npm config set prefix).", prefix=prefix, moved=moved))
    return True


def _move_to_home(run: Run, npm: str, prefix: Path, env: MutableMapping[str, str]) -> Path | None:
    """`~/.local` as this account's prefix, with its `bin` first on PATH; None when it is not ours to move."""
    home = env.get("HOME")
    if not home or _node_prefix(npm) != _resolved(prefix):
        return None
    target = Path(home) / ".local"
    if run([npm, "config", "set", "prefix", str(target)], env).returncode != 0:
        return None
    folder = str(target / "bin")
    # The setup's own environment and this process's: the gateway service and the doctor
    # look the new `openclaw` up on the process PATH, not in the setup's copy of it.
    for values in (env, os.environ):
        path = values.get("PATH", "")
        if folder not in path.split(os.pathsep):
            values["PATH"] = folder + os.pathsep + path if path else folder
    return target


def _node_prefix(npm: str) -> Path:
    """The folder npm picks when nobody set one: the Node install it belongs to (`<prefix>/bin/npm`).

    The `bin` folder is resolved, never `npm` itself: that is a link into
    `lib/node_modules/npm/bin/`, and following it lands three folders too deep.
    """
    return _resolved(Path(npm).parent).parent


def _resolved(path: Path) -> Path:
    """`path` with links followed, or as given when the filesystem cannot answer."""
    try:
        return path.resolve()
    except OSError:  # nexgen: allow reason="an unresolvable path compares as written"
        return path
