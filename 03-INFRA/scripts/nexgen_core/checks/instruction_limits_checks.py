"""How much of the AGENTS.md bootstrap each installed harness actually reads.

nexgen: one-job reason="the byte limit past which a harness silently truncates the shared bootstrap, and the check that measures every installed one against it"
One job: the byte limit past which a harness silently truncates the
instructions file it loads, and the doctor check that measures what each
installed harness reads and reports how close the bootstrap is to the tightest
of them. It exists because a harness drops everything past its limit without
saying so: a rule written past that point stops existing for one runtime only,
which is the silent divergence every other check in this engine is built to
prevent.

Verified on 2026-11-14 against each harness's own documentation, and carried
per reader in `INSTRUCTION_READERS` together with the URL it came from:

- Antigravity truncates a rule file over 24,000 bytes.
- Codex reads `project_doc_max_bytes`, 32 KiB by default.
- The DeepSeek harness budgets 65,536 bytes per instruction chain.
- Claude Code loads a CLAUDE.md of up to 4 MiB and skips a larger one whole.

OpenCode documents no byte limit for its AGENTS.md, so it has no row: an
invented number turns into a fault nobody can fix, which is worse than not
asking the question at all.

It lives beside `instructions_checks.py` rather than inside it because that
file is already at its split-queue ceiling, and a new family of numbers would
only make it longer.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import NamedTuple

from nexgen_core.i18n import t
from nexgen_core.paths import canonical_instructions
from nexgen_core.report import CheckOutcome, Severity


class InstructionReader(NamedTuple):
    """One harness, the instructions file it loads at session start, and the
    documented byte limit past which it stops reading that file."""

    name: str
    binary: str
    relative_path: Path
    limit_bytes: int
    source: str
    #: True when `relative_path` is a pointer the engine writes, and the rules the
    #: harness really reads are the canonical file that pointer names.
    pointer_to_canonical: bool = False


#: What each harness reads and how much of it, verified against the harness's
#: own documentation on 2026-11-14. `source` is carried into the doctor's detail
#: line on purpose: a limit nobody can check is a number someone will eventually
#: change in the wrong direction.
INSTRUCTION_READERS: tuple[InstructionReader, ...] = (
    InstructionReader(
        "Antigravity",
        "agy",
        Path(".gemini") / "config" / "AGENTS.md",
        24000,
        "https://antigravity.google/docs/rules/",
    ),
    InstructionReader(
        "Codex",
        "codex",
        Path(".codex") / "AGENTS.md",
        32768,
        "https://learn.chatgpt.com/docs/agent-configuration/agents-md",
    ),
    InstructionReader(
        "DeepSeek harness",
        "dsh",
        Path(".dsh") / "AGENTS.md",
        65536,
        "https://github.com/deepseek-ai/deepseek-harness/blob/master/"
        "packages/context/agent-instructions/README.md",
    ),
    InstructionReader(
        "Claude Code",
        "claude",
        Path(".claude") / "CLAUDE.md",
        4194304,
        "https://code.claude.com/docs/en/memory",
        # Claude reads the pointer and then the file it names: measuring the ~300-byte
        # pointer left this row unable to bind on any machine.
        pointer_to_canonical=True,
    ),
)

#: The check's id: the doctor, its tests and the report name the same finding.
READER_LIMITS_CHECK_ID = "instructions.reader_limits"

#: Percent of a harness's verified limit at which the doctor warns, while the
#: content can still be moved out instead of silently dropped by the harness.
READER_LIMITS_WARN_PERCENT = 90


def _reader_paths(home: Path, canon: Path) -> list[tuple[InstructionReader, Path]]:
    """The instructions file each installed harness would read right now.

    A harness that isn't installed here, or that has no such file yet, reads
    nothing: it is left out, so it neither bounds the bootstrap nor gets
    reported. The path is measured as the harness reads it, so on Windows
    without the symlink privilege the identical copy the guard leaves behind
    is measured, not the link that would have been there.

    A harness that loads a pointer file reads the canonical file the pointer
    names, so that is what is measured: the pointer itself carries no rules and
    would keep its own row green at any bootstrap size.
    """
    found: list[tuple[InstructionReader, Path]] = []
    for reader in INSTRUCTION_READERS:
        if shutil.which(reader.binary) is None:
            continue
        path = home / reader.relative_path
        if not path.is_file():
            continue
        found.append((reader, _file_whose_bytes_are_read(reader, path, canon)))
    return found


def _file_whose_bytes_are_read(reader: InstructionReader, path: Path, canon: Path) -> Path:
    """The file this harness's rules really live in: the pointer's target when it is one."""
    if not reader.pointer_to_canonical or not canon.is_file():
        return path
    try:
        names_canon = str(canon) in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # nexgen: allow reason="an unreadable pointer names nothing; its own size is then the only measurable fact"
        return path
    return canon if names_canon else path


def _reader_states(measured: list[tuple[InstructionReader, int]]) -> tuple[list[str], list[str]]:
    """Which harnesses are already past their limit, and which are close to it.

    One measurement, two questions: the doctor turns the first list into a
    broken finding and the second into a warning before any byte is dropped.
    """
    over: list[str] = []
    near: list[str] = []
    for reader, size in measured:
        label = f"{reader.name} ({size}/{reader.limit_bytes} bytes)"
        if size > reader.limit_bytes:
            over.append(label)
        elif size * 100 >= reader.limit_bytes * READER_LIMITS_WARN_PERCENT:
            near.append(label)
    return over, near


def _reader_limits_detail(measured: list[tuple[InstructionReader, int]]) -> str:
    """Where each enforced limit comes from: the doctor quotes the source, so
    the number can be checked instead of trusted."""
    return t(
        "Verified read limits: {limits}",
        limits="; ".join(
            f"{reader.name} {reader.limit_bytes} ({reader.source})" for reader, _size in measured
        ),
    )


def check_bootstrap_reader_limits(vault_data: Path, home: Path) -> CheckOutcome | None:
    """The bootstrap must fit inside the read limit of every installed harness.

    Each harness that loads an instructions file at session start reads only
    the first N bytes of it and drops the rest without saying so: a rule
    written past the limit stops existing for that runtime alone. Comparing
    what each installed harness reads with its own limit makes the tightest
    installed limit the binding one for the shared bootstrap.

    At 90% of a limit it warns, while content can still be moved out; past the
    limit it is broken. The verified numbers and the URL each came from live in
    this module's header and in `INSTRUCTION_READERS`. OpenCode documents no
    byte limit for its AGENTS.md, so its size is never enforced here.

    A harness that is not installed, or that has no instructions file yet, is
    not reported: it reads nothing, so nothing is truncated.
    """
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None  # already reported by instructions.canonical_present

    readers = _reader_paths(home, canon)
    if not readers:
        return None  # no harness here reads a bootstrap: nothing binds it
    measured = [(reader, path.stat().st_size) for reader, path in readers]

    over, near = _reader_states(measured)
    if over:
        return CheckOutcome(
            id=READER_LIMITS_CHECK_ID,
            severity=Severity.BROKEN,
            message=t(
                "The AGENTS.md bootstrap is over the read limit of {readers}: everything past "
                "it is dropped silently, so those rules never reach that runtime.",
                readers=", ".join(over),
            ),
            action=t("Move task-specific content into a load-on-demand note, pulled in only when needed."),
            detail=_reader_limits_detail(measured),
        )
    if near:
        return CheckOutcome(
            id=READER_LIMITS_CHECK_ID,
            severity=Severity.WARN,
            message=t(
                "The AGENTS.md bootstrap is within {percent}% of the read limit of {readers}.",
                percent=READER_LIMITS_WARN_PERCENT, readers=", ".join(near),
            ),
            action=t("Move task-specific content into a load-on-demand note before the next addition."),
            detail=_reader_limits_detail(measured),
        )
    tightest_reader, tightest_size = min(measured, key=lambda item: item[0].limit_bytes)
    return CheckOutcome(
        id=READER_LIMITS_CHECK_ID,
        severity=Severity.OK,
        message=t(
            "AGENTS.md fits every installed harness ({size}/{limit} bytes at the tightest limit, {name})",
            size=tightest_size, limit=tightest_reader.limit_bytes, name=tightest_reader.name,
        ),
        detail=_reader_limits_detail(measured),
    )
