#!/usr/bin/env sh
# nexgen: allow reason="bootstrap launcher must locate or install Python before Python exists"
# NeXgen Engine — bootstrap.
#
# This file does exactly one thing: find Python and hand off to it (telling it
# which engine it is, when no other one is declared).
# Everything else — prerequisites, vault structure, detected assistants,
# recommended profile — lives in 03-INFRA/scripts/nexgen_core/bootstrap.py,
# in one place only, because you can't use Python to check that Python
# exists, but everything that follows can be checked once Python is found.
#
#   sh install.sh                      preflight, questions, and next step
#   sh install.sh --check              checks only, no questions and no writes
#   sh install.sh --role server        headless Linux always-on gateway
#   sh install.sh --role node          connects as a node to an existing gateway
#   sh install.sh --role node --exec-policy full
#                                     the same, and this PC never stops on an exec approval
#   sh install.sh --role standalone    local PC with its own gateway
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
BOOTSTRAP="$ROOT/03-INFRA/scripts/nexgen_core/bootstrap.py"

if [ ! -f "$BOOTSTRAP" ]; then
    echo "NeXgen: this clone is incomplete, missing $BOOTSTRAP" >&2
    exit 1
fi

# The engine is this clone unless one is declared or installed in ~/.nexgen-engine:
# a client clones into ~/KnowledgeVault, and without this the guard units it writes
# named an engine folder that does not exist.
if [ -z "${AGENT_ENGINE_ROOT:-}" ] && [ ! -d "$HOME/.nexgen-engine/03-INFRA" ]; then
    AGENT_ENGINE_ROOT="$ROOT/03-INFRA"
    export AGENT_ENGINE_ROOT
fi

FOUND=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            exec "$candidate" "$BOOTSTRAP" "$@"
        fi
        FOUND="$("$candidate" --version 2>&1)"
    fi
done

# If system python is < 3.11 or missing, try user-mode uv without reading other user configs
if command -v uv >/dev/null 2>&1; then
    UV_PY="$(UV_NO_CONFIG=1 uv python find '>=3.11' 2>/dev/null || true)"
    if [ -z "$UV_PY" ]; then
        echo "NeXgen: installing Python in user mode via uv..." >&2
        UV_NO_CONFIG=1 uv python install 3.12 >/dev/null 2>&1 || true
        UV_PY="$(UV_NO_CONFIG=1 uv python find '>=3.11' 2>/dev/null || true)"
    fi
    if [ -n "$UV_PY" ] && [ -x "$UV_PY" ]; then
        exec "$UV_PY" "$BOOTSTRAP" "$@"
    fi
fi

echo "NeXgen: needs Python 3.11 or later.${FOUND:+ Found: $FOUND.}" >&2
echo "Install it in user mode (curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.12) or via your system's package manager." >&2
echo "Then rerun this script." >&2
exit 1
