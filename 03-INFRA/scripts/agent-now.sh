#!/usr/bin/env bash
# agent-now — deterministic current date/time source for agents.
# Training data lies about "what year is it"; this doesn't. Read this before
# any recency-sensitive judgment call, search query, or deadline check
# (see AGENTS.md > Operating Style, and firecrawl.md).
#
# Usage: agent-now [--json]
# Output is always JSON; --json is accepted for symmetry with call sites
# that pass it explicitly.
set -u

local_time="$(date +%Y-%m-%dT%H:%M:%S%z)"
today="$(date +%Y-%m-%d)"
year="$(date +%Y)"
weekday="$(LC_TIME=C date +%A)"
tz="$(date +%Z)"

printf '{"source": "system_clock", "local_time": "%s", "date": "%s", "year": %s, "weekday": "%s", "timezone": "%s"}\n' \
  "$local_time" "$today" "$year" "$weekday" "$tz"
