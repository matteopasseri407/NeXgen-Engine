"""The command surface after the 2026-10-24 retirement of the lane prototypes.

The retired verbs (relay, mail, calendar, workflow, patch, jobs, research,
explore, the lane's own MCP server) must not come back by accident: they were
removed because nothing on the owner's machine ever used them, and a help line
is the cheapest way to notice that one quietly returned.
"""
from __future__ import annotations

import re

import pytest

from nexgen_local import cli

#: Every verb the lane offers. A new verb is added here on purpose.
EXPECTED_COMMANDS = [
    "run", "eval", "drive-propose", "drive-upload", "drive-mcp", "doctor", "tier", "sup",
]

#: Verbs retired on 2026-10-24. None of them may reach the subcommand list.
RETIRED_COMMANDS = [
    "relay", "mail-propose", "mails", "mail-send", "cal-propose", "cals", "cal-apply",
    "wf-propose", "wfs", "wf-run", "propose", "proposals", "apply", "mcp",
    "research", "explore", "close",
]


def _subcommands(text: str) -> list[str]:
    """The verbs argparse prints in braces at the top of a help screen."""
    match = re.search(r"\{([a-z0-9,\-]+)\}", text)
    assert match, f"no command list in help output:\n{text}"
    return match.group(1).split(",")


def test_top_level_help_lists_only_the_kept_verbs(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out

    assert _subcommands(printed) == EXPECTED_COMMANDS
    for retired in RETIRED_COMMANDS:
        assert retired not in _subcommands(printed)


def test_the_description_states_the_read_only_surface(capsys) -> None:
    """The one-line description is what every launcher shows; it must not sell writes."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    printed = capsys.readouterr().out
    assert "sola lettura" in printed
    for claim in ("proposte patch", "invia", "pubblica"):
        assert claim not in printed


def test_eval_lists_only_the_surviving_suites(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["eval", "--help"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert "{capability,traps,all}" in printed
    for gone in ("patch", "jobs", "agent"):
        assert f",{gone}" not in printed
