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

They are transitional. Once the new engine has run once, it replaces the
symlinks with its own launchers and nothing points here any more. They can go
when no machine is left on the old release.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            continue
        path.write_text(content, encoding="utf-8")
        if path.suffix == ".sh":
            path.chmod(path.stat().st_mode | 0o111)
        changed.append(path.name)
    return changed


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
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]
    if stale:
        print("Out of step with the table: " + ", ".join(sorted(stale)), file=sys.stderr)
        return 1
    print("Every launcher matches the table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
