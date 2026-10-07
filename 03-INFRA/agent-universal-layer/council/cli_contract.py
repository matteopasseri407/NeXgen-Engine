"""Do the vendor CLIs still accept the flags a Council seat passes them?

A seat is a vendor CLI started with a fixed set of flags (``_build_seat_command``). Those CLIs
ship a new version every few weeks and rename or drop flags; a seat then fails at run time,
after a round has already spent subscription quota. This takes the flags the Council really
passes, from the same builder so the two cannot drift, and looks for each one in the CLI's own
``--help``. It starts no model, spends no quota and sends nothing to a vendor.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from routing import _run_probe
from seat_process import SUPPORTED_CLIS, _build_seat_command

#: Where each CLI lists the flags of the command a seat runs (not always the top-level help).
HELP_ARGV: dict[str, list[str]] = {
    "codex": ["codex", "exec", "--help"],
    "claude": ["claude", "--help"],
    "agy": ["agy", "--help"],
    # Never an opencode command without --standalone: without it `run` talks to the background service.
    "opencode": ["opencode", "run", "--standalone", "--help"],
    "ollama": ["ollama", "run", "--help"],
}

_FLAG = re.compile(r"-{1,2}[A-Za-z][\w-]*")


@dataclass(frozen=True)
class ContractResult:
    cli: str
    #: ``ok``, ``drift`` (a flag is gone from the help), ``unprobed`` (the help could not be read)
    #: or ``absent`` (the CLI is not installed here, which is not a fault).
    status: str
    detail: str
    missing: tuple[str, ...] = ()


def flags_passed(cli: str) -> list[str]:
    """Every flag a seat for ``cli`` is started with, effort flag included."""
    seat = {"cli": cli, "model": "contract-probe", "reasoning_effort": "high"}
    with tempfile.TemporaryDirectory(prefix="council-contract-") as scratch:
        session_dir = Path(scratch)
        # The codex builder copies the real credentials into the seat's private directory;
        # pointing it at an empty one keeps this probe from touching them at all.
        empty_home = session_dir / "empty-codex-home"
        empty_home.mkdir()
        previous = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(empty_home)
        try:
            argv = _build_seat_command(seat, "probe", session_dir).argv
        finally:
            if previous is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = previous
    return sorted({token for token in argv[1:] if _FLAG.fullmatch(token)})


def flag_listed(flag: str, help_text: str) -> bool:
    """Is ``flag`` named in the help, as a whole flag and not as a prefix of another one?"""
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


def check_cli(cli: str, run_probe=_run_probe) -> ContractResult:
    if shutil.which(cli) is None:
        return ContractResult(cli, "absent", f"'{cli}' is not installed on this host")
    ok, output = run_probe(HELP_ARGV[cli])
    if not ok:
        return ContractResult(cli, "unprobed", f"'{' '.join(HELP_ARGV[cli])}' did not answer: {output[:120]}")
    passed = flags_passed(cli)
    missing = tuple(flag for flag in passed if not flag_listed(flag, output))
    if missing:
        return ContractResult(
            cli, "drift", f"the installed '{cli}' no longer lists: {', '.join(missing)}", missing,
        )
    return ContractResult(cli, "ok", f"all {len(passed)} flags a seat passes are still listed")


def check_all(run_probe=_run_probe) -> list[ContractResult]:
    return [check_cli(cli, run_probe) for cli in SUPPORTED_CLIS]
