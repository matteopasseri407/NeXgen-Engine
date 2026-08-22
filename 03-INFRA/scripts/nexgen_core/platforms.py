#!/usr/bin/env python3
"""What is actually supported, stated once.

The README used to describe platform support in prose, in two languages,
and the prose had drifted from the code: `install.sh` handled Linux, macOS
and Windows, the README declared two of them, and macOS was in the code
without ever being declared anywhere. It also said the tools were verified
"on Fedora", which stopped being true when the maintainer's machine moved to
Ubuntu — a sentence that tells the reader about the author's laptop rather
than about the product.

So the status lives here, as data, and the README section is generated from
it. A test fails when the two disagree, which is the only thing that keeps a
support claim honest over time: someone has to change the data to change the
promise.

The wording of each status is deliberately about evidence, not confidence.
"Released" says what was run, not how sure anybody feels.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Support:
    """One row: what it is, how far it got, and on what evidence."""

    name: str
    status: str
    evidence: str


#: Operating systems. Anything the installer can start on belongs here,
#: declared — including the ones that are only partly proven. A platform
#: present in the code and absent from this table is the bug this file
#: exists to prevent.
OPERATING_SYSTEMS: tuple[Support, ...] = (
    Support(
        "Linux",
        "released",
        "the platform this is developed and used on daily; the full cycle (install, "
        "alignment, doctor, grooming, council, update) runs here and in CI",
    ),
    Support(
        "Windows",
        "released",
        "verified on real hardware and in CI; full native Python execution, dual launchers, and complete CLI alignment",
    ),
    Support(
        "macOS",
        "untested",
        "shares the POSIX paths with Linux and should work, but nobody has run it "
        "end to end; treat a failure here as expected, and reporting it as useful",
    ),
)

#: The assistants the engine configures. "Half supported" is worse than
#: unsupported, so a runtime that is not complete says exactly where it stops.
RUNTIMES: tuple[Support, ...] = (
    Support("Claude Code", "complete", "instructions, MCP connectors, skills, guardrails"),
    Support("Codex", "complete", "instructions, MCP connectors, skills"),
    Support("OpenCode", "complete", "instructions, MCP connectors, skills"),
    Support(
        "Antigravity",
        "complete",
        "instructions, MCP connectors, skills, and a Council seat; the seat was "
        "unblocked on 2026-08-22 with a stateless invocation (agy --model ... "
        "--disable-slash-commands --new-project --sandbox -p <prompt>) verified live "
        "with a nonce prompt",
    ),
)

START = "<!-- platform-status:start -->"
END = "<!-- platform-status:end -->"


def render_markdown() -> str:
    """The section as it must appear, between its two markers."""
    lines = [START, "", "| System | Status | On what evidence |", "|---|---|---|"]
    lines += [f"| {s.name} | **{s.status}** | {s.evidence} |" for s in OPERATING_SYSTEMS]
    lines += ["", "| Assistant | Status | What is covered |", "|---|---|---|"]
    lines += [f"| {s.name} | **{s.status}** | {s.evidence} |" for s in RUNTIMES]
    lines += ["", END]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Prints the table. Used by the docs check and by anyone who asks."""
    print(render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
