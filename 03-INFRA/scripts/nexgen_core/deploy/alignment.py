"""Which engine commit this machine, the Vault pin and the deployed vault-mcp are on.

nexgen: one-job reason="read the three commit identities of an installation that runs a server, and say which one lags"
One job: read the three commits and return them as plain data. It decides
nothing about severity and prints nothing: the doctor turns the reading into a
finding, the deploy command turns it into a comparison, and both read the same
two files and one route.

The three identities exist because a Cloud-Server install is two halves that
move on separate schedules. The engine on the VPS updates itself on a timer; the
container under `03-INFRA/deploy/` is only rebuilt when someone rebuilds it, so
before this module the two could be months apart with nothing measuring it.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from nexgen_core.deploy.probe import get_json
from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_vault_data
from nexgen_core.processes import no_window_kwargs

#: Where the updater of a split installation records the commit it installed,
#: for every other machine to read. Written through `vault-push`, never by hand.
PIN_PATH = Path("99-INDEX") / "ENGINE-PIN.txt"

#: The field the vault-mcp health route publishes, and the path it publishes it on.
COMMIT_FIELD = "engine_commit"
HEALTH_PATH = "/healthz"

#: A short commit, which is git's own abbreviation and what the route exposes.
SHORT_COMMIT = 9

#: Where the running stack answers when nothing on the machine says otherwise:
#: the port `03-INFRA/deploy/vault-mcp/docker-compose.yml` publishes.
DEFAULT_MCP_URL = "http://127.0.0.1:8081/mcp"

#: How long the health route may take. The doctor runs this check beside the
#: others, inside a budget of a few seconds.
HEALTH_TIMEOUT_SECONDS = 2.5

GIT_TIMEOUT_SECONDS = 20.0

#: The Vault says which install this is in one line of its profile.
_MODE_LINE = re.compile(r"^-\s*\*\*Mode\*\*:\s*(.+)$", re.MULTILINE)
CLOUD_SERVER_MODE = "[CLOUD-SERVER]"


@dataclass(frozen=True)
class Alignment:
    """The three commits an installation has to keep in step.

    `answered` separates "the service is not there" from "the service is there
    and does not say what built it": the first is a reading a down server gives,
    the second is an image built before this release.
    """

    engine: str | None
    pin: str | None
    deployed: str | None
    answered: bool
    detail: str = ""

    @property
    def aligned(self) -> bool:
        """Engine, pin and deployed server all name the same commit."""
        return bool(self.engine) and self.deployed == self.engine and self.pin == self.engine


def short_commit(value: str | None) -> str | None:
    """The nine characters every comparison here uses, or None when empty."""
    text = (value or "").strip().lower()
    return text[:SHORT_COMMIT] or None


def _read_optional(path: Path) -> str | None:
    """The file's text, or None when there is no file. Anything else raises.

    "Absent" is a reading these two identities both have to be able to give; a
    file that exists and cannot be read is a broken machine, and the doctor's
    own schedule turns that into a finding instead of into a quiet None.
    """
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def engine_commit(engine_root: Path) -> str | None:
    """The commit this machine's engine checkout is on, or None outside a checkout.

    A package install has no commit to compare, and that is not a fault: the
    caller reports it as undetermined instead of inventing an identity.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(engine_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=GIT_TIMEOUT_SECONDS, check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):  # nexgen: allow reason="a machine with no git, or a repository that hangs, has no commit to report; the caller says undetermined"
        return None
    return short_commit(proc.stdout) if proc.returncode == 0 else None


def pinned_commit(vault_data: Path) -> str | None:
    """The commit the Vault says the fleet installed, or None when nothing pinned it."""
    text = _read_optional(Path(vault_data) / PIN_PATH)
    return short_commit(text)


def cloud_server_install(vault_data: Path) -> bool:
    """Does this Vault's profile declare a Cloud-Server install?

    The template ships `[LOCAL-ONLY] or [CLOUD-SERVER]`, and a half-filled
    profile is a machine that never chose: only an exact value counts, so an
    unedited template is not read as a server.
    """
    text = _read_optional(Path(vault_data) / "99-INDEX" / "USER-PROFILE.md")
    if text is None:
        return False
    match = _MODE_LINE.search(text)
    if not match:
        return False
    return match.group(1).strip().strip("`'\" ").upper() == CLOUD_SERVER_MODE


def deploy_matters(home: Path | None = None, vault_data: Path | None = None) -> bool:
    """Is this a machine whose containers have to keep up with the engine?

    Two answers count, and the role file wins: the server that runs the stack,
    and any machine whose profile declares a Cloud-Server install (the
    workstation whose doctor is supposed to notice the server lagging).
    """
    from nexgen_core import roles

    home_dir = resolve_home(home)
    vault = resolve_vault_data(home_dir, vault_data)
    return roles.declared_role(home=home_dir, vault_root=vault) == roles.SERVER_ROLE or cloud_server_install(vault)


def mcp_url(environ: dict[str, str] | None = None) -> str:
    """The address `vault-library` already has on this machine.

    The manifest mounts the connector from `VAULT_LIBRARY_URL`, so reading the
    same variable means the doctor probes the server the CLIs actually talk to,
    tunnel included, instead of a second address maintained here.
    """
    values = os.environ if environ is None else environ
    return values.get("VAULT_LIBRARY_URL") or DEFAULT_MCP_URL


def health_url(mcp_address: str) -> str:
    """The unauthenticated route on the same origin as the MCP endpoint."""
    parsed = urlparse(mcp_address)
    if not parsed.scheme or not parsed.netloc:
        return f"{DEFAULT_MCP_URL.rsplit('/mcp', 1)[0]}{HEALTH_PATH}"
    return f"{parsed.scheme}://{parsed.netloc}{HEALTH_PATH}"


def measure(
    *,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    mcp_address: str | None = None,
    timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> Alignment:
    """Read all three identities. Never raises: a reading is not a failure."""
    root = resolve_engine_root() if engine_root is None else engine_root
    vault = resolve_vault_data() if vault_data is None else vault_data
    address = mcp_address or mcp_url()
    body, detail = get_json(health_url(address), timeout=timeout)
    deployed = short_commit(str(body.get(COMMIT_FIELD) or "")) if body else None
    return Alignment(
        engine=engine_commit(root),
        pin=pinned_commit(vault),
        deployed=deployed,
        answered=body is not None,
        # One doctor line, not a stack trace: an operating system's own wording
        # can run for a paragraph, and the reader needs the route and the fact.
        detail=detail[:120],
    )
