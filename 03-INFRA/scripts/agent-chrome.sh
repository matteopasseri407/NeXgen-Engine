#!/usr/bin/env bash
# One visible Chrome with local CDP, shared by the human and every agent.
#
# Two callers, two contracts:
#
#   agent-chrome [args...]  someone wants a WINDOW (an icon was clicked, a URL
#                           was opened, a PWA was started). Always hand off to
#                           the Chrome binary: Chromium's own single-instance
#                           IPC raises the running browser instead of starting
#                           a second one, so a window always appears.
#   agent-chrome --ensure   an agent only wants the shared browser to EXIST.
#                           Exit 0 when CDP already answers, without opening an
#                           extra window.
#
# The bare call used to be the --ensure call. That is why clicking the Chrome
# icon did nothing at all whenever a windowless Chrome still held :9222: the
# script exited 0 and the desktop considered the launch successful.
#
# Why this script has to own EVERY entry point, PWA launchers included: the
# first Chrome process to open the shared profile decides whether :9222 exists
# for the rest of the session. A launcher that starts Chrome without the
# debugging port wins that race, and every later launch -- this script included
# -- is reduced to an IPC handoff, which cannot add the port to a browser that
# is already running. The shared-browser lane is then dead until Chrome is
# restarted. agent-sync therefore routes the Chrome-generated chrome-*.desktop
# PWA entries through this script too, and this script reports the lost race
# instead of failing silently.
set -eu

profile="${AGENT_CHROME_PROFILE:-$HOME/.config/chrome-agent-debug}"
standard_profile="$HOME/.config/google-chrome"

mode="window"
case "${1:-}" in
  --ensure) mode="ensure"; shift ;;
  --heal)   mode="heal";   shift ;;
esac

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

cdp_is_up() {
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsS --max-time 2 http://127.0.0.1:9222/json/version >/dev/null 2>&1
}

# The PID holding the shared profile, if any. Chromium writes SingletonLock as
# a symlink whose target ends in "-<pid>"; a stale lock from a crash points at
# a PID that no longer exists, which is why this probes the process too.
profile_owner_pid() {
  local target pid
  target="$(readlink "$profile/SingletonLock" 2>/dev/null)" || return 1
  pid="${target##*-}"
  case "$pid" in
    '' | *[!0-9]*) return 1 ;;
  esac
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s\n' "$pid"
}

# Reported, never notified: `agent-doctor` is the only judge and the
# _send_healthcheck step in agent_sync.py is the only megaphone
# (03-INFRA/agent-guardians-map.md). The matching doctor check is what reaches
# the user; this line is for whoever reads the journal or runs it by hand.
report_lost_race() {
  printf '%s\n' \
    "agent-chrome: Chrome (PID $1) holds the shared profile without the CDP port." \
    "Agents cannot attach until it is restarted: run 'agent-chrome --heal'." >&2
}

# --ensure is the idempotent "make sure the shared browser is up" call. It is
# the only mode allowed to do nothing, and only when CDP actually answers.
if [ "$mode" = "ensure" ] && cdp_is_up; then
  exit 0
fi

if owner="$(profile_owner_pid)"; then
  if cdp_is_up; then
    # Healthy shared browser. --heal would be destructive for no reason.
    if [ "$mode" = "heal" ]; then
      printf '%s\n' "agent-chrome: CDP already answers on 127.0.0.1:9222; nothing to heal." >&2
      exit 0
    fi
  elif [ "$mode" = "heal" ]; then
    printf '%s\n' "agent-chrome: restarting Chrome (PID $owner) to restore the CDP port." >&2
    kill "$owner" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$owner" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$owner" 2>/dev/null; then
      kill -9 "$owner" 2>/dev/null || true
    fi
  else
    # Handing off to a Chrome without CDP still gives the human their window,
    # so this is a warning and not a refusal -- but it must never be silent.
    report_lost_race "$owner"
  fi
fi

if [ -d "$standard_profile" ] && [ ! -L "$standard_profile" ]; then
  printf '%s\n' \
    "agent-chrome: the existing Chrome profile is still in the standard directory." \
    "Close Chrome, migrate that profile to $profile, and point $standard_profile to it before retrying." >&2
  exit 2
fi

# A PWA window carries Chrome's own crx_<app-id> WM_CLASS, which is what keeps
# its icon separate in the dock. Forcing the browser class here would collapse
# every PWA into the Chrome icon, so --class is only for real browser windows.
class_args=(--class=Google-chrome)
for arg in "$@"; do
  case "$arg" in
    --app-id=* | --app=*) class_args=() ;;
  esac
done

mkdir -p "$profile"
exec "$chrome" \
  ${class_args[@]+"${class_args[@]}"} \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$profile" \
  --no-first-run \
  "$@"
