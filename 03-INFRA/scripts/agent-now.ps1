#!/usr/bin/env pwsh
# agent-now — deterministic current date/time source for agents.
# Training data lies about "what year is it"; this doesn't. Read this before
# any recency-sensitive judgment call, search query, or deadline check
# (see AGENTS.md > Operating Style, and firecrawl.md).
#
# Usage: agent-now.ps1 [-Json]
# Output is always JSON; -Json is accepted for symmetry with call sites
# that pass it explicitly.
param([switch]$Json)

$now = Get-Date
$payload = [ordered]@{
    source     = "system_clock"
    local_time = $now.ToString("yyyy-MM-ddTHH:mm:sszzz")
    date       = $now.ToString("yyyy-MM-dd")
    year       = $now.Year
    weekday    = $now.DayOfWeek.ToString()
    timezone   = [System.TimeZoneInfo]::Local.Id
}
$payload | ConvertTo-Json -Compress
