#!/usr/bin/env bash
# agent-now — deterministic current date/time source for agents.
# Training data lies about "what year is it"; this doesn't. Read this before
# any recency-sensitive judgment call, search query, or deadline check
# (see AGENTS.md > Operating Style, and firecrawl.md).
#
# Usage: agent-now [--json|--human|--shell]
# Default output is JSON. --human and --shell are alternate renderings of the
# same snapshot for people and for eval'ing scripts respectively.
set -eu

FORMAT="json"
case "${1:-}" in
  ""|--json|json) FORMAT="json" ;;
  --human|human) FORMAT="human" ;;
  --shell|shell) FORMAT="shell" ;;
  -h|--help|help)
    printf 'Usage: agent-now [--json|--human|--shell]\n'
    exit 0
    ;;
  *)
    printf 'agent-now: unknown option: %s\n' "$1" >&2
    exit 2
    ;;
esac

td_prop() {
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl show -p "$1" --value 2>/dev/null || true
  fi
}

local_iso="$(date -Is)"
utc_iso="$(date -u -Is)"
epoch_seconds="$(date +%s)"
local_date="$(date +%F)"
local_time="$(date +%T%z)"
year="$(date +%Y)"
weekday="$(LC_TIME=C date +%A)"
timezone="$(td_prop Timezone)"
ntp_synchronized="$(td_prop NTPSynchronized)"
local_rtc="$(td_prop LocalRTC)"
can_ntp="$(td_prop CanNTP)"
ntp_enabled="$(td_prop NTP)"

[ -n "$timezone" ] || timezone="$(date +%Z)"
[ -n "$ntp_synchronized" ] || ntp_synchronized="unknown"
[ -n "$local_rtc" ] || local_rtc="unknown"
[ -n "$can_ntp" ] || can_ntp="unknown"
[ -n "$ntp_enabled" ] || ntp_enabled="unknown"

case "$FORMAT" in
  human)
    printf 'Local: %s\nUTC:   %s\nTZ:    %s\nNTP:   synchronized=%s enabled=%s can_ntp=%s local_rtc=%s\n' \
      "$local_iso" "$utc_iso" "$timezone" "$ntp_synchronized" "$ntp_enabled" "$can_ntp" "$local_rtc"
    ;;
  shell)
    printf 'AGENT_NOW_LOCAL_ISO=%q\n' "$local_iso"
    printf 'AGENT_NOW_UTC_ISO=%q\n' "$utc_iso"
    printf 'AGENT_NOW_TIMEZONE=%q\n' "$timezone"
    printf 'AGENT_NOW_EPOCH_SECONDS=%q\n' "$epoch_seconds"
    printf 'AGENT_NOW_DATE=%q\n' "$local_date"
    printf 'AGENT_NOW_TIME=%q\n' "$local_time"
    printf 'AGENT_NOW_YEAR=%q\n' "$year"
    printf 'AGENT_NOW_WEEKDAY=%q\n' "$weekday"
    printf 'AGENT_NOW_NTP_SYNCHRONIZED=%q\n' "$ntp_synchronized"
    ;;
  json)
    printf '{\n'
    printf '  "source": "system_clock",\n'
    printf '  "local_time": "%s",\n' "$local_iso"
    printf '  "utc_time": "%s",\n' "$utc_iso"
    printf '  "timezone": "%s",\n' "$timezone"
    printf '  "epoch_seconds": %s,\n' "$epoch_seconds"
    printf '  "date": "%s",\n' "$local_date"
    printf '  "time": "%s",\n' "$local_time"
    printf '  "year": %s,\n' "$year"
    printf '  "weekday": "%s",\n' "$weekday"
    printf '  "ntp_synchronized": "%s",\n' "$ntp_synchronized"
    printf '  "ntp_enabled": "%s",\n' "$ntp_enabled"
    printf '  "can_ntp": "%s",\n' "$can_ntp"
    printf '  "local_rtc": "%s"\n' "$local_rtc"
    printf '}\n'
    ;;
esac
