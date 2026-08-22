#!/usr/bin/env sh
# NeXgen Engine — generated, do not edit by hand.
#
# 'agent-open-folder' as the previous release installed it. It holds no logic: it finds a
# Python and hands over to 'nexgen tool open'. Regenerate with:
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
        exec "$candidate" "$ENTRY" "tool" "open" "$@"
    fi
done

echo "NeXgen: Python 3 is not on this system's PATH." >&2
exit 1
