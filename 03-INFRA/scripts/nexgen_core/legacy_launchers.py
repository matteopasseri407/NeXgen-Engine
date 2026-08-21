#!/usr/bin/env python3
"""The launcher files the previous version's symlinks point at.

A machine already running the released engine has `~/.local/bin/agent-sync`
pointing into the engine checkout, at `03-INFRA/scripts/agent-sync.sh`. That
machine updates itself: it fetches the new tree and then runs its own
commands. If those files are not there afterwards, every command on that
machine points at nothing — and the very first casualty is the alignment run
the updater performs immediately after merging, so the update reports failure
having already replaced the tree.

So the files stay. They are allowed to, because they hold no decisions: each
one finds a Python and hands over, and they are generated from the same table
as everything else. Two shells that contain no logic cannot drift apart, which
is the only reason the rule against hand-kept twins tolerates them at all.

They are transitional, and the expiry is written down rather than remembered:
`REMOVE_AFTER` below. The release preflight refuses to tag a version that has
passed it, so at that release someone has to decide out loud — delete them, or
move the date because a machine is genuinely still behind. Compatibility that
nobody is forced to look at again is how it becomes permanent.

They cannot delete themselves on the machine that uses them: they are tracked
files in the engine's own git checkout, and removing them locally would leave
that checkout dirty, which is the exact condition the updater refuses to work
on. So they go the only way that is safe — deleted upstream, in a release, and
every machine receives their absence through the ordinary update.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

#: The version after which these must not ship any more.
#:
#: A machine that has run the new engine once no longer points at these files
#: at all — `install_shims()` replaces the previous release's symlinks with
#: real launchers of its own. So the condition to remove them is not "my
#: machines have migrated", it is "no machine is left on the old release": a
#: straggler jumping straight from v1 into a tree without them would land on
#: exactly the breakage these exist to prevent.
#:
#: `nexgen doctor` reports, per machine, whether the takeover has completed
#: there. When every machine says yes, delete this module and the twenty files
#: it writes, and drop the launcher checks from the release preflight.
REMOVE_AFTER = "2.3.0"

#: The names the previous release installed into `~/.local/bin` as symlinks
#: into the engine checkout. `vault-ocr-local` is absent on purpose: it was
#: always bring-your-own and pointed at the user's own data, not at the engine.
LEGACY_SCRIPT_NAMES = (
    "agent-sync",
    "agent-doctor",
    "agent-chrome",
    "agent-now",
    "agent-open-folder",
    "council",
    "firecrawl-local",
    "nexgen-update",
    "vault-push",
    "vault-groom",
)

_POSIX = """#!/usr/bin/env sh
# NeXgen Engine — generated, do not edit by hand.
#
# '{name}' as the previous release installed it. It holds no logic: it finds a
# Python and hands over to '{verbs}'. Regenerate with:
#   python3 03-INFRA/scripts/nexgen_core/legacy_launchers.py --write
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENTRY="$SCRIPT_DIR/nexgen_core/cli/__init__.py"

if [ ! -f "$ENTRY" ]; then
    echo "NeXgen: engine files are missing at $ENTRY" >&2
    exit 1
fi

for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        exec "$candidate" "$ENTRY" {args}"$@"
    fi
done

echo "NeXgen: Python 3 is not on this system's PATH." >&2
exit 1
"""

_WINDOWS = """# NeXgen Engine — generated, do not edit by hand.
#
# '{name}' as the previous release installed it. It holds no logic: it finds a
# Python and hands over to '{verbs}'. Regenerate with:
#   python3 03-INFRA/scripts/nexgen_core/legacy_launchers.py --write

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$entry = Join-Path $scriptDir 'nexgen_core\\cli\\__init__.py'

if (-not (Test-Path $entry)) {{
    [Console]::Error.WriteLine("NeXgen: engine files are missing at $entry")
    exit 1
}}

foreach ($candidate in @(@('py', '-3'), @('python3'), @('python'))) {{
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {{ continue }}
    $forward = $candidate[1..($candidate.Length - 1)] + @($entry){ps_args} + $args
    & $exe @forward
    exit $LASTEXITCODE
}}

[Console]::Error.WriteLine("NeXgen: Python 3 is not on this system's PATH.")
exit 1
"""


def _verbs_for(name: str) -> list[str]:
    """The verbs this historical name puts in front of `nexgen`."""
    from nexgen_core.shims import LEGACY_ALIASES

    return list(LEGACY_ALIASES.get(name, []))


def render(name: str, *, windows: bool) -> str:
    """The launcher body for one historical name."""
    verbs = _verbs_for(name)
    spoken = "nexgen " + " ".join(verbs) if verbs else "nexgen"
    if windows:
        ps_args = "".join(f" + @('{v}')" for v in verbs)
        return _WINDOWS.format(name=name, verbs=spoken, ps_args=ps_args)
    args = "".join(f'"{v}" ' for v in verbs)
    return _POSIX.format(name=name, verbs=spoken, args=args)


def _normalised(text: str) -> str:
    """Line endings do not count as a difference.

    Git checks `.ps1` out with CRLF on every platform, by this repository's
    own attributes. Comparing raw bytes would report every Windows launcher as
    out of step forever.
    """
    return text.replace("\r\n", "\n")


def matches(path: Path, content: str) -> bool:
    """Is the launcher on disk the one the table describes?"""
    return path.is_file() and _normalised(path.read_text(encoding="utf-8")) == _normalised(content)


def expected_files(scripts_dir: Path) -> dict[Path, str]:
    """Every launcher that must exist, and exactly what it must contain."""
    out: dict[Path, str] = {}
    for name in LEGACY_SCRIPT_NAMES:
        out[scripts_dir / f"{name}.sh"] = render(name, windows=False)
        out[scripts_dir / f"{name}.ps1"] = render(name, windows=True)
    return out


def write_all(scripts_dir: Path) -> list[str]:
    """Writes the launchers, returning the ones that needed changing."""
    changed: list[str] = []
    for path, content in expected_files(scripts_dir).items():
        if matches(path, content):
            continue
        path.write_text(content, encoding="utf-8")
        if path.suffix == ".sh":
            path.chmod(path.stat().st_mode | 0o111)
        changed.append(path.name)
    return changed


def is_expired(version: str) -> bool:
    """Has the engine passed the version by which these should have gone?"""
    from nexgen_core.release import newer_version

    return newer_version(version, REMOVE_AFTER)


def takeover_complete(home: Path | None = None) -> tuple[bool, list[str]]:
    """On this machine, is anything still reaching the engine through these?

    The previous release put symlinks in `~/.local/bin` pointing at
    `<engine>/03-INFRA/scripts/<name>.sh`. The new engine replaces them with
    launchers of its own the first time it runs. So a symlink still pointing
    into the scripts directory means this machine has not completed the
    handover — and that these files are still load-bearing here.
    """
    from nexgen_core.paths import resolve_home

    bin_dir = resolve_home(home) / ".local" / "bin"
    still_linked = []
    for name in LEGACY_SCRIPT_NAMES:
        launcher = bin_dir / name
        if not launcher.is_symlink():
            continue
        try:
            target = launcher.readlink()
        except OSError:
            continue
        if target.name.endswith((".sh", ".ps1")):
            still_linked.append(name)
    return not still_linked, sorted(still_linked)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="nexgen-legacy-launchers",
        description="Regenerates the launchers the previous release's symlinks point at.",
    )
    parser.add_argument("--write", action="store_true", help="Write them instead of only checking")
    args = parser.parse_args(argv)

    scripts_dir = SCRIPTS_DIR
    if args.write:
        changed = write_all(scripts_dir)
        print(f"{len(changed)} launcher(s) rewritten" if changed else "Already up to date")
        return 0

    stale = [
        path.name
        for path, content in expected_files(scripts_dir).items()
        if not matches(path, content)
    ]
    if stale:
        print("Out of step with the table: " + ", ".join(sorted(stale)), file=sys.stderr)
        return 1
    print("Every launcher matches the table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
