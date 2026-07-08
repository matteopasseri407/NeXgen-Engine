#!/usr/bin/env bash
# Launcher only: the logic lives in agent_sync.py (single cross-platform
# source, shared with agent-sync.ps1). See agent_sync.py --help for modes.
#
# readlink -f (not plain BASH_SOURCE/dirname): this launcher is normally
# reached through a ~/.local/bin/agent-sync symlink, and BASH_SOURCE does
# NOT follow symlinks -- dirname on the raw value would resolve to the
# symlink's own directory, not this script's, and miss agent_sync.py
# entirely.
set -eu
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/agent_sync.py" "$@"
