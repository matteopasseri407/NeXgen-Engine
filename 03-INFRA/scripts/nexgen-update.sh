#!/usr/bin/env bash
# Cross-platform logic lives in nexgen_core/updater.py. Resolve this launcher's real
# directory even when agent-sync installed it as ~/.local/bin/nexgen-update.
set -eu

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  SOURCE_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in
    /*) ;;
    *) SOURCE="$SOURCE_DIR/$SOURCE" ;;
  esac
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
exec python3 "$SCRIPT_DIR/nexgen_core/updater.py" "$@"
