#!/usr/bin/env bash
set -eu

profile="${AGENT_CHROME_PROFILE:-$HOME/.config/chrome-agent-debug}"
standard_profile="$HOME/.config/google-chrome"

chrome=""
for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
  if command -v "$candidate" >/dev/null 2>&1; then
    chrome="$(command -v "$candidate")"
    break
  fi
done

if [ -z "$chrome" ]; then
  printf '%s\n' "agent-chrome: Chrome or Chromium is not installed." >&2
  exit 127
fi

if [ "$#" -eq 0 ] && command -v curl >/dev/null 2>&1 \
  && curl -fsS --max-time 2 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
  exit 0
fi

if [ -d "$standard_profile" ] && [ ! -L "$standard_profile" ]; then
  printf '%s\n' \
    "agent-chrome: the existing Chrome profile is still in the standard directory." \
    "Close Chrome, migrate that profile to $profile, and point $standard_profile to it before retrying." >&2
  exit 2
fi

mkdir -p "$profile"
exec "$chrome" \
  --class=Google-chrome \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$profile" \
  --no-first-run \
  "$@"
