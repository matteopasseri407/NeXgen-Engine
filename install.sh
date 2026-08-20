#!/usr/bin/env sh
# NeXgen Engine — bootstrap.
#
# This file does exactly one thing: find Python and hand off to it.
# Everything else — prerequisites, vault structure, detected assistants,
# recommended profile — lives in 03-INFRA/scripts/nexgen_core/bootstrap.py,
# in one place only, because you can't use Python to check that Python
# exists, but everything that follows can be checked once Python is found.
#
#   sh install.sh            preflight, questions, and next step
#   sh install.sh --check    checks only, no questions and no writes
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
BOOTSTRAP="$ROOT/03-INFRA/scripts/nexgen_core/bootstrap.py"

if [ ! -f "$BOOTSTRAP" ]; then
    echo "NeXgen: this clone is incomplete, missing $BOOTSTRAP" >&2
    exit 1
fi

for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            exec "$candidate" "$BOOTSTRAP" "$@"
        fi
        FOUND="$("$candidate" --version 2>&1)"
    fi
done

echo "NeXgen: needs Python 3.11 or later.${FOUND:+ Found: $FOUND.}" >&2
echo "Install it from your system's package manager and rerun this script." >&2
exit 1
